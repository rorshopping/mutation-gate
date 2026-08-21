public enum DemoMath {
    /// Clamps `v` into the inclusive range `lo...hi`.
    public static func clamp(_ v: Int, lo: Int, hi: Int) -> Int {
        if v < lo { return lo }
        if v > hi { return hi }
        return v
    }

    /// True when `x` equals zero.
    public static func isZero(_ x: Int) -> Bool {
        return !(x != 0)
    }

    /// Sums values, skipping negatives.
    public static func sumPositive(_ values: [Int]) -> Int {
        var total = 0
        for v in values {
            if v > 0 {
                total += v
            }
        }
        return total
    }

    public static func label(for score: Int) -> String {
        if score >= 90 && score <= 100 {
            return "excellent"
        }
        guard score > 50 else { return "poor" }
        return "ok"
    }
}
