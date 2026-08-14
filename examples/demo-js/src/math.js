export function clamp(value, lo, hi) {
  if (value < lo) return lo;
  if (value > hi) return hi;
  return value;
}

export function average(values) {
  if (values.length === 0) return 0;
  let sum = 0;
  for (const v of values) {
    sum += v;
  }
  return sum / values.length;
}

export function isEven(n) {
  return n % 2 === 0;
}

export function countAbove(values, threshold) {
  let count = 0;
  for (const v of values) {
    if (v > threshold) {
      count += 1;
    }
  }
  return count;
}

export function describe(n) {
  if (n < 0) return "negative";
  if (n === 0) return "zero";
  return "positive";
}
