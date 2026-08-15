#pragma once

namespace calc {

int clamp(int value, int lo, int hi);
double average(const double* values, int count);
bool isEven(int n);
int countAbove(const double* values, int count, double threshold);
const char* describe(int n);

}  // namespace calc
