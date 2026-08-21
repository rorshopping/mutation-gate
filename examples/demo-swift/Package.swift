// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "DemoMath",
    targets: [
        .target(name: "DemoMath"),
        .testTarget(name: "DemoMathTests", dependencies: ["DemoMath"])
    ]
)
