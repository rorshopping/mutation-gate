package calc;

import java.util.List;

/** Small library with real behavior for mutation testing. */
public final class Calculator {

    private Calculator() {}

    public static int clamp(int value, int lo, int hi) {
        if (value < lo) return lo;
        if (value > hi) return hi;
        return value;
    }

    public static double average(List<Double> values) {
        if (values.isEmpty()) return 0.0;
        double sum = 0.0;
        for (double v : values) {
            sum += v;
        }
        return sum / values.size();
    }

    public static boolean isEven(int n) {
        return n % 2 == 0;
    }

    public static int countAbove(List<Double> values, double threshold) {
        int count = 0;
        for (double v : values) {
            if (v > threshold) {
                count += 1;
            }
        }
        return count;
    }

    public static String describe(int n) {
        if (n == 0) return "zero";
        if (n < 0) return "negative";
        return "positive";
    }
}
