import XCTest
@testable import DemoMath

final class DemoMathTests: XCTestCase {
    func testClamp() {
        XCTAssertEqual(DemoMath.clamp(-5, lo: 0, hi: 10), 0)
        XCTAssertEqual(DemoMath.clamp(5, lo: 0, hi: 10), 5)
        XCTAssertEqual(DemoMath.clamp(50, lo: 0, hi: 10), 10)
        // Boundary values pin the comparison operators exactly.
        XCTAssertEqual(DemoMath.clamp(0, lo: 0, hi: 10), 0)
        XCTAssertEqual(DemoMath.clamp(10, lo: 0, hi: 10), 10)
        XCTAssertEqual(DemoMath.clamp(-1, lo: 0, hi: 10), 0)
        XCTAssertEqual(DemoMath.clamp(11, lo: 0, hi: 10), 10)
    }

    func testIsZero() {
        XCTAssertTrue(DemoMath.isZero(0))
        XCTAssertFalse(DemoMath.isZero(3))
    }

    func testSumPositive() {
        XCTAssertEqual(DemoMath.sumPositive([1, -2, 3]), 4)
        XCTAssertEqual(DemoMath.sumPositive([-1, -2]), 0)
        // Zero is not positive — pins `v > 0` against `v >= 0`.
        XCTAssertEqual(DemoMath.sumPositive([0, 4]), 4)
    }

    func testLabel() {
        // Every boundary of the grading conditions.
        XCTAssertEqual(DemoMath.label(for: 90), "excellent")
        XCTAssertEqual(DemoMath.label(for: 100), "excellent")
        XCTAssertEqual(DemoMath.label(for: 101), "ok")
        XCTAssertEqual(DemoMath.label(for: 89), "ok")
        XCTAssertEqual(DemoMath.label(for: 51), "ok")
        XCTAssertEqual(DemoMath.label(for: 50), "poor")
    }
}
