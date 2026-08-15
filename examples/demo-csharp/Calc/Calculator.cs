namespace Calc;

public static class Calculator
{
    public static int Clamp(int value, int lo, int hi)
    {
        if (value < lo) return lo;
        if (value > hi) return hi;
        return value;
    }

    public static double Average(List<double> values)
    {
        if (values.Count == 0) return 0;
        double sum = 0;
        foreach (double v in values)
        {
            sum += v;
        }
        return sum / values.Count;
    }

    public static bool IsEven(int n)
    {
        return n % 2 == 0;
    }

    public static int CountAbove(List<double> values, double threshold)
    {
        int count = 0;
        foreach (double v in values)
        {
            if (v > threshold)
            {
                count += 1;
            }
        }
        return count;
    }

    public static string Describe(int n)
    {
        if (n == 0) return "zero";
        if (n < 0) return "negative";
        return "positive";
    }
}
