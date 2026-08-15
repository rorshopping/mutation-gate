using Calc;

int failures = 0;

void Check(bool ok, string label)
{
    if (!ok)
    {
        Console.WriteLine($"FAIL: {label}");
        failures += 1;
    }
}

Check(Calculator.Clamp(5, 0, 10) == 5, "clamp middle");
Check(Calculator.Clamp(-3, 0, 10) == 0, "clamp below lo");
Check(Calculator.Clamp(42, 0, 10) == 10, "clamp above hi");
Check(Calculator.Clamp(0, 0, 10) == 0, "clamp lo edge");
Check(Calculator.Clamp(10, 0, 10) == 10, "clamp hi edge");

Check(Calculator.Average(new List<double> { 1, 2, 3, 4 }) == 2.5, "average basic");
Check(Calculator.Average(new List<double>()) == 0, "average empty");

Check(Calculator.IsEven(0), "isEven zero");
Check(!Calculator.IsEven(1), "isEven odd");
Check(Calculator.IsEven(10), "isEven even");

Check(Calculator.CountAbove(new List<double> { 1, 2, 3, 4, 5 }, 3) == 2, "countAbove");
Check(Calculator.CountAbove(new List<double>(), 1) == 0, "countAbove empty");

Check(Calculator.Describe(0) == "zero", "describe zero");
Check(Calculator.Describe(-1) == "negative", "describe negative");
Check(Calculator.Describe(5) == "positive", "describe positive");

Console.WriteLine(failures == 0 ? "ALL PASS" : $"{failures} FAILURES");
Environment.Exit(failures == 0 ? 0 : 1);
