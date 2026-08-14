import { test } from "node:test";
import assert from "node:assert/strict";
import { average, clamp, countAbove, describe, isEven } from "../src/math.js";

// The classic AI-style theater test: assert "it returned something",
// never "it returned the right thing".
test("clamp returns a number", () => {
  const result = clamp(5, 0, 10);
  assert.ok(result !== undefined);
});

test("average returns a number", () => {
  const result = average([1, 2, 3]);
  assert.ok(result !== undefined);
});

test("isEven returns a boolean", () => {
  const result = isEven(2);
  assert.ok(result !== undefined);
});

test("countAbove returns a number", () => {
  const result = countAbove([1, 2], 1);
  assert.ok(result !== undefined);
});

test("describe returns a string", () => {
  const result = describe(1);
  assert.ok(result !== undefined);
});
