#include "math.h"

namespace calc {

int clamp(int value, int lo, int hi) {
    if (value < lo) return lo;
    if (value > hi) return hi;
    return value;
}

double average(const double* values, int count) {
    if (count == 0) return 0.0;
    double sum = 0.0;
    for (int i = 0; i < count; ++i) {
        sum += values[i];
    }
    return sum / count;
}

bool isEven(int n) {
    return n % 2 == 0;
}

int countAbove(const double* values, int count, double threshold) {
    int n = 0;
    for (int i = 0; i < count; ++i) {
        if (values[i] > threshold) {
            n += 1;
        }
    }
    return n;
}

const char* describe(int n) {
    if (n == 0) return "zero";
    if (n < 0) return "negative";
    return "positive";
}

}  // namespace calc
