import AppKit
import Combine
import SwiftUI

/// Live wrapper: observes the store so the popover re-renders on refresh.
struct PopoverRoot: View {
    @ObservedObject var store: StatusStore

    var body: some View {
        ContentView(
            status: store.status,
            errorText: store.errorText,
            instanceName: store.instanceName,
            updatedText: updatedText,
            onRefresh: { store.refresh() },
            onQuit: { NSApp.terminate(nil) }
        )
    }

    private var updatedText: String {
        guard let updated = store.lastUpdated else { return "updating…" }
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        return "updated \(formatter.string(from: updated))"
    }
}

/// NSStatusItem + NSPopover shell, following CodexBar's controller shape
/// (status item with template icon, optional attention text, transient panel).
final class StatusItemController: NSObject {
    private let store: StatusStore
    private let statusItem: NSStatusItem
    private let popover = NSPopover()
    private var timer: Timer?
    private var cancellables: Set<AnyCancellable> = []

    init(store: StatusStore) {
        self.store = store
        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()

        if let button = statusItem.button {
            let icon = NSImage(
                systemSymbolName: "infinity",
                accessibilityDescription: "Arthur Loop"
            )
            icon?.isTemplate = true
            button.image = icon
            button.imagePosition = .imageLeft
            button.target = self
            button.action = #selector(togglePopover(_:))
            button.toolTip = "Arthur Loop status"
        }

        popover.behavior = .transient
        popover.animates = false
        popover.contentViewController = NSHostingController(rootView: PopoverRoot(store: store))

        store.$status
            .receive(on: DispatchQueue.main)
            .sink { [weak self] status in self?.updateButton(status) }
            .store(in: &cancellables)

        store.refresh()
        timer = Timer.scheduledTimer(withTimeInterval: store.refreshSeconds, repeats: true) { [weak store] _ in
            store?.refresh()
        }
    }

    private func updateButton(_ status: LoopStatus?) {
        guard let button = statusItem.button else { return }
        let count = status?.attentionCount ?? 0
        button.title = count > 0 ? " \(count)" : ""
        button.font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold)
        let state = status?.state ?? "unknown"
        button.toolTip = "Arthur Loop — \(state)" + (count > 0 ? " · \(count) item(s) need you" : "")
    }

    @objc private func togglePopover(_ sender: Any?) {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            popover.performClose(sender)
        } else {
            store.refresh()
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: StatusItemController?
    private let store: StatusStore

    init(store: StatusStore) {
        self.store = store
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller = StatusItemController(store: store)
    }
}
