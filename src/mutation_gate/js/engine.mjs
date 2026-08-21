// Babel-based JS/TS mutator for mutation-gate.
// Usage:
//   node engine.mjs <projectRoot> <file>            one file -> JSON array on stdout
//   node engine.mjs <projectRoot> --batch f1 f2 ..  NDJSON: {"file":..,"mutants":[..]} per line
// Mutant shape: [{ operator, line, before, after, source }]
// Babel packages are resolved from the PROJECT's node_modules (createRequire),
// so this engine has no install-time deps of its own.
//
// Performance notes: each candidate mutates a deep clone produced by
// t.cloneNode (far cheaper than a JSON roundtrip), located via the key path
// recorded during traversal instead of walking both trees in parallel. Batch
// mode amortizes runtime startup + Babel require across many files.

import { createRequire } from "module";
import { readFileSync } from "fs";

const [, , projectRoot, ...rest] = process.argv;
const req = createRequire(projectRoot + "/package.json");

let parse, traverse, generate, t;
try {
  parse = req("@babel/parser").parse;
  traverse = req("@babel/traverse").default;
  generate = req("@babel/generator").default;
  t = req("@babel/types");
} catch (err) {
  console.error(
    "mutation-gate: JS target needs @babel/parser, @babel/traverse, @babel/generator, @babel/types installed in the project.\n" +
      "  npm install -D @babel/parser @babel/traverse @babel/generator @babel/types"
  );
  process.exit(2);
}

const COMPARISON_FLIPS = {
  "<": "<=",
  "<=": "<",
  ">": ">=",
  ">=": ">",
  "==": "!=",
  "!=": "==",
  "===": "!==",
  "!==": "===",
};
const BINOP_FLIPS = {
  "+": "-",
  "-": "+",
  "*": "/",
  "/": "*",
  "%": "/",
  "**": "*",
  "|": "&",
  "&": "|",
  "^": "|",
};
const AUG_FLIPS = {
  "+=": "-=",
  "-=": "+=",
  "*=": "/=",
  "/=": "*=",
  "%=": "/=",
  "&=": "|=",
  "|=": "&=",
};

const REMOVABLE_STMT = new Set([
  "VariableDeclaration",
  "ExpressionStatement",
  "ReturnStatement",
  "ThrowStatement",
  "IfStatement",
  "WhileStatement",
  "ForStatement",
  "SwitchStatement",
  "TryStatement",
  "BreakStatement",
  "ContinueStatement",
]);

const SKIP_STRING_CONTEXTS = new Set([
  "ImportDeclaration",
  "ExportNamedDeclaration",
  "ExportAllDeclaration",
  "TSLiteralType",
]);

function snippet(node) {
  const program = {
    type: "Program",
    sourceType: "module",
    body:
      node.type === "File" || node.type === "Program"
        ? node.body
        : node.type.endsWith("Statement") || node.type.endsWith("Declaration")
        ? [node]
        : [{ type: "ExpressionStatement", expression: node }],
  };
  const out = generate({ type: "File", program }, { retainLines: false }).code;
  return out
    .replace(/;$/, "")
    .replace(/^"/, '"')
    .trim();
}

function parseSource(source, label) {
  try {
    return parse(source, { sourceType: "unambiguous", plugins: ["typescript", "jsx"] });
  } catch {
    try {
      return parse(source, { sourceType: "unambiguous", plugins: ["jsx"] });
    } catch {
      return null;
    }
  }
}

// Collect candidate mutation sites. `parents` maps node -> {parent,listKey,key}
// so each candidate also carries the root->node key path into any deep clone.
function collectCandidates(ast) {
  const parents = new Map();
  const candidates = [];
  const add = (op, node, apply) => {
    const steps = [];
    let cur = node;
    while (parents.has(cur)) {
      const e = parents.get(cur);
      steps.push([e.listKey, e.key]);
      cur = e.parent;
    }
    steps.reverse();
    candidates.push({ operator: op, line: node.loc.start.line, node, steps, apply });
  };

  traverse(ast, {
    enter(path) {
      if (path.parent && !parents.has(path.node)) {
        parents.set(path.node, { parent: path.parent, listKey: path.listKey ?? null, key: path.key ?? null });
      }
    },
    BinaryExpression(path) {
      const op = path.node.operator;
      if (COMPARISON_FLIPS[op]) {
        add("comparison", path.node, (n) => {
          n.operator = COMPARISON_FLIPS[op];
          return n;
        });
      } else if (BINOP_FLIPS[op]) {
        add("binop", path.node, (n) => {
          n.operator = BINOP_FLIPS[op];
          return n;
        });
      }
    },
    LogicalExpression(path) {
      add("boolop", path.node, (n) => {
        n.operator = n.operator === "&&" ? "||" : "&&";
      });
    },
    AssignmentExpression(path) {
      const op = path.node.operator;
      if (AUG_FLIPS[op]) {
        add("aug_assign", path.node, (n) => {
          n.operator = AUG_FLIPS[op];
          return n;
        });
      }
    },
    BooleanLiteral(path) {
      add("bool_literal", path.node, (n) => {
        n.value = !n.value;
      });
    },
    NumericLiteral(path) {
      if (path.parent.type === "TSLiteralType") return;
      add("num_literal", path.node, (n) => {
        n.value = n.value + 1;
      });
    },
    StringLiteral(path) {
      if (SKIP_STRING_CONTEXTS.has(path.parent.type)) return;
      if (path.parent.type === "ExpressionStatement" && path.parent.directive) return;
      add("str_literal", path.node, (n) => {
        n.value = n.value === "" ? "MUTANT" : "";
      });
    },
    UnaryExpression(path) {
      if (path.node.operator !== "!") return;
      add("remove_not", path.node, (n) => n.argument);
    },
    IfStatement(path) {
      add("negate_condition", path.node, (n) => {
        n.test = t.unaryExpression("!", n.test);
        return n;
      });
    },
    ConditionalExpression(path) {
      add("negate_condition", path.node, (n) => {
        n.test = t.unaryExpression("!", n.test);
        return n;
      });
    },
    ReturnStatement(path) {
      if (!path.node.argument) return;
      add("return_none", path.node, (n) => {
        n.argument = null;
        return n;
      });
    },
  });

  // Second pass for remove_stmt, appended after the operator candidates so
  // ordering matches the historical two-pass behavior.
  traverse(ast, {
    Statement(path) {
      if (!REMOVABLE_STMT.has(path.node.type)) return;
      const parent = path.parentPath;
      const body = parent.node.body;
      if (!Array.isArray(body) || body.length <= 1) return;
      add("remove_stmt", path.node, (n, ctx) => {
        if (ctx && ctx.listKey != null && ctx.key != null) {
          ctx.parent[ctx.listKey].splice(ctx.key, 1);
        }
        return null;
      });
    },
  });

  return candidates;
}

// Navigate a clone to {parent, listKey, key, node} matching the original's
// key path (structures are identical by construction).
function locateClone(cloneRoot, steps) {
  let cur = cloneRoot;
  for (let i = 0; i < steps.length - 1; i++) {
    const [listKey, key] = steps[i];
    cur = listKey != null ? cur[listKey][key] : cur[key];
  }
  const [listKey, key] = steps[steps.length - 1];
  const parent = cur;
  const node = listKey != null ? parent[listKey][key] : parent[key];
  return { parent, listKey, key, node };
}

function mutateAst(source) {
  const ast = parseSource(source);
  if (!ast) return null;
  const candidates = collectCandidates(ast);

  const mutants = [];
  const seen = new Set();
  let id = 0;

  for (const c of candidates) {
    const clone = t.cloneNode(ast, true);
    const loc = c.steps.length ? locateClone(clone, c.steps) : null;
    if (!loc || !loc.node) continue;
    const replacement = c.apply(loc.node, loc);
    if (replacement && replacement !== loc.node) {
      if (loc.listKey != null) {
        loc.parent[loc.listKey][loc.key] = replacement;
      } else if (loc.key != null) {
        loc.parent[loc.key] = replacement;
      }
    }
    const mutated = generate(clone, { retainLines: false }).code;
    if (mutated === source) continue;
    if (seen.has(mutated)) continue;
    seen.add(mutated);

    const before = snippet(c.node);
    const afterNode = replacement && replacement !== loc.node ? replacement : loc.node;
    const after = replacement === null ? "(removed)" : snippet(afterNode);

    mutants.push({
      id: id++,
      operator: c.operator,
      line: c.line,
      before,
      after,
      source: mutated,
    });
  }
  return mutants;
}

const batch = rest[0] === "--batch";
const files = batch ? rest.slice(1) : [rest[0]];

for (const file of files) {
  let source;
  try {
    source = readFileSync(file, "utf8");
  } catch (err) {
    if (batch) {
      process.stdout.write(JSON.stringify({ file, error: `read failed: ${err.message}` }) + "\n");
      continue;
    }
    console.error(`mutation-gate: could not read ${file}`);
    process.exit(3);
  }
  const mutants = mutateAst(source);
  if (mutants === null) {
    if (batch) {
      process.stdout.write(JSON.stringify({ file, error: "parse failed" }) + "\n");
      continue;
    }
    console.error(`mutation-gate: could not parse ${file}`);
    process.exit(3);
  }
  if (batch) {
    process.stdout.write(JSON.stringify({ file, mutants }) + "\n");
  } else {
    process.stdout.write(JSON.stringify(mutants));
  }
}
