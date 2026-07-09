import AppKit
import SwiftUI

// Modes:
//   ArthurBar                              live menu bar app (config-driven)
//   ArthurBar --root PATH [--name N]       live, against one instance
//   ArthurBar --snapshot out.png [...]     render the popover to a PNG and exit
//                                          (pixel-perfect screenshots, no screen-recording permission)

struct CLIOptions {
    var snapshotPath: String?
    var root: String?
    var name: String?
    var arthur: String?
}

func parseOptions() -> CLIOptions {
    var options = CLIOptions()
    var iterator = CommandLine.arguments.dropFirst().makeIterator()
    while let arg = iterator.next() {
        switch arg {
        case "--snapshot": options.snapshotPath = iterator.next()
        case "--root": options.root = iterator.next()
        case "--name": options.name = iterator.next()
        case "--arthur": options.arthur = iterator.next()
        case "--help", "-h":
            print("usage: ArthurBar [--root PATH] [--name NAME] [--arthur PATH] [--snapshot OUT.png]")
            exit(0)
        default:
            FileHandle.standardError.write(Data("unknown argument: \(arg)\n".utf8))
            exit(2)
        }
    }
    return options
}

@MainActor
func writeSnapshot(_ options: CLIOptions) -> Never {
    let root = options.root ?? ConfigLoader.load().instance.root
    let name = options.name ?? URL(fileURLWithPath: root).lastPathComponent
    do {
        let status = try ArthurRunner.fetch(root: root, arthur: options.arthur)
        let view = ContentView(
            status: status,
            errorText: nil,
            instanceName: name,
            updatedText: "updated just now",
            onRefresh: nil,
            onQuit: nil
        )
        .background(Color(nsColor: .windowBackgroundColor))

        let renderer = ImageRenderer(content: view)
        renderer.scale = 2.0
        guard let image = renderer.nsImage,
              let tiff = image.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:])
        else {
            FileHandle.standardError.write(Data("error: could not render snapshot\n".utf8))
            exit(1)
        }
        let out = URL(fileURLWithPath: options.snapshotPath!)
        try png.write(to: out)
        print(out.path)
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("error: \(error.localizedDescription)\n".utf8))
        exit(1)
    }
}

let options = parseOptions()

if options.snapshotPath != nil {
    _ = NSApplication.shared
    MainActor.assumeIsolated {
        writeSnapshot(options)
    }
}

let loaded = ConfigLoader.load()
var instance = loaded.instance
if let root = options.root {
    instance = InstanceConfig(name: options.name, root: root, arthur: options.arthur)
}
let store = StatusStore(instance: instance, refreshSeconds: loaded.refreshSeconds)

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate(store: store)
app.delegate = delegate
app.run()
