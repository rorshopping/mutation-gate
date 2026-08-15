#include "math.h"

#include <cmath>
#include <cstdio>
#include <string>

int failures = 0;

void check(bool ok, const char* label) {
    if (!ok) {
        std::printf("FAIL: %s\n", label);
        failures += 1;
    }
}

int main() {
    double arr[] = {1.0, 2.0, 3.0, 4.0};
    double none[] = {};

    check(calc::clamp(5, 0, 10) == 5, "clamp middle");
    check(calc::clamp(-3, 0, 10) == 0, "clamp below lo");
    check(calc::clamp(42, 0, 10) == 10, "clamp above hi");
    check(calc::clamp(0, 0, 10) == 0, "clamp lo edge");
    check(calc::clamp(10, 0, 10) == 10, "clamp hi edge");

    check(std::fabs(calc::average(arr, 4) - 2.5) < 1e-9, "average basic");
    check(calc::average(none, 0) == 0.0, "average empty");

    check(calc::isEven(0), "isEven zero");
    check(!calc::isEven(1), "isEven odd");
    check(calc::isEven(10), "isEven even");

    check(calc::countAbove(arr, 4, 3.0) == 1, "countAbove");
    check(calc::countAbove(none, 0, 1.0) == 0, "countAbove empty");

    check(std::string(calc::describe(0)) == "zero", "describe zero");
    check(std::string(calc::describe(-1)) == "negative", "describe negative");
    check(std::string(calc::describe(5)) == "positive", "describe positive");

    std::printf(failures == 0 ? "ALL PASS\n" : "%d FAILURES\n", failures);
    return failures == 0 ? 0 : 1;
}
