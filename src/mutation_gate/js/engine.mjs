// Babel-based JS/TS mutator for mutation-gate.
// Usage: node engine.mjs <projectRoot> <file>
// Reads the file, emits a JSON array of mutants to stdout:
//   [{ operator, line, before, after, source }]
// Babel packages are resolved from the PROJECT's node_modules (createRequire),
// so this engine has no install-time deps of its own.

import { createRequire } from "module";
import { readFileSync } from "fs";

const [, , projectRoot, file] = process.argv;
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

const source = readFileSync(file, "utf8");

let ast;
try {
  ast = parse(source, { sourceType: "unambiguous", plugins: ["typescript", "jsx"] });
} catch {
  try {
    ast = parse(source, { sourceType: "unambiguous", plugins: ["jsx"] });
  } catch {
    console.error(`mutation-gate: could not parse ${file}`);
    process.exit(3);
  }
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

const candidates = [];

function add(op, node, apply) {
  candidates.push({ operator: op, line: node.loc.start.line, node, apply });
}

traverse(ast, {
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

traverse(ast, {
  Statement(path) {
    if (!REMOVABLE_STMT.has(path.node.type)) return;
    const parent = path.parentPath;
    const body = parent.node.body;
    if (!Array.isArray(body) || body.length <= 1) return;
    add("remove_stmt", path.node, (n, ctx) => {
      if (ctx && ctx.index !== null) {
        ctx.parent[ctx.key].splice(ctx.index, 1);
      }
      return null;
    });
  },
});

function cloneNodeDeep(node) {
  return JSON.parse(JSON.stringify(node));
}

// Walk the original tree and its clone in parallel until the target is found.
// Returns { node, parent, key, index } locating the target inside the clone.
function findClone(node, target, clone, parent = null, key = null, index = null) {
  if (node === target) return { node: clone, parent, key, index };
  if (!node || typeof node !== "object" || !node.type) return null;
  for (const k of Object.keys(node)) {
    const val = node[k];
    const cval = clone ? clone[k] : undefined;
    if (Array.isArray(val)) {
      if (!Array.isArray(cval)) continue;
      for (let j = 0; j < val.length; j++) {
        if (val[j] && typeof val[j] === "object" && val[j].type) {
          const r = findClone(val[j], target, cval[j], clone, k, j);
          if (r) return r;
        }
      }
    } else if (val && typeof val === "object" && val.type) {
      const r = findClone(val, target, cval, clone, k, null);
      if (r) return r;
    }
  }
  return null;
}

const mutants = [];
const seen = new Set();
let id = 0;

for (const c of candidates) {
  const clone = cloneNodeDeep(ast);
  const found = findClone(ast, c.node, clone);
  if (!found) continue;
  const replacement = c.apply(found.node, found);
  if (replacement && replacement !== found.node) {
    if (found.index !== null) {
      found.parent[found.key][found.index] = replacement;
    } else if (found.key !== null) {
      found.parent[found.key] = replacement;
    }
  }
  const mutated = generate(clone, { retainLines: false }).code;
  if (mutated === source) continue;
  if (seen.has(mutated)) continue;
  seen.add(mutated);

  const before = snippet(c.node);
  const afterNode = replacement && replacement !== found.node ? replacement : found.node;
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

process.stdout.write(JSON.stringify(mutants));
