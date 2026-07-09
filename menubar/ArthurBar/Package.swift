// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "ArthurBar",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "ArthurBar",
            path: "Sources/ArthurBar"
        ),
        .testTarget(
            name: "ArthurBarTests",
            dependencies: ["ArthurBar"],
            path: "Tests/ArthurBarTests"
        ),
    ]
)
