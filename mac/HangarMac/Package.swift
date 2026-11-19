// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "HangarMac",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "HangarMac", path: "Sources/HangarMac"),
    ]
)
