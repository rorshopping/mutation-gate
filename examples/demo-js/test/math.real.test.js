import { test } from "node:test";
import assert from "node:assert/strict";
import { average, clamp, countAbove, describe, isEven } from "../src/math.js";

test("clamp bounds", () => {
  assert.equal(clamp(5, 0, 10), 5);
  assert.equal(clamp(-1, 0, 10), 0);
  assert.equal(clamp(11, 0, 10), 10);
  assert.equal(clamp(0, 0, 10), 0);
  assert.equal(clamp(10, 0, 10), 10);
});

test("average basic + empty", () => {
  assert.equal(average([1, 2, 3]), 2);
  assert.equal(average([5]), 5);
  assert.equal(average([]), 0);
});

test("isEven", () => {
  assert.equal(isEven(0), true);
  assert.equal(isEven(1), false);
  assert.equal(isEven(10), true);
});

test("countAbove", () => {
  assert.equal(countAbove([1, 5, 9, 12], 5), 2);
  assert.equal(countAbove([], 5), 0);
});

test("describe", () => {
  assert.equal(describe(-3), "negative");
  assert.equal(describe(0), "zero");
  assert.equal(describe(3), "positive");
});
