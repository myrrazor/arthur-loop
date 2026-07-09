import SwiftUI

/// Quota bar. The 6pt fully-rounded single-Canvas treatment follows CodexBar's
/// UsageProgressBar (MIT, © Peter Steinberger — github.com/steipete/CodexBar),
/// which draws with one Canvas pass to stay crisp inside menu surfaces.
struct UsageBar: View {
    let fraction: Double
    let tint: Color

    var body: some View {
        Canvas { context, size in
            let radius = size.height / 2
            let track = Path(
                roundedRect: CGRect(origin: .zero, size: size),
                cornerRadius: radius
            )
            context.fill(track, with: .color(.primary.opacity(0.12)))

            let clamped = max(0, min(1, fraction))
            guard clamped > 0 else { return }
            let fillWidth = max(size.height, size.width * clamped)
            let fill = Path(
                roundedRect: CGRect(x: 0, y: 0, width: fillWidth, height: size.height),
                cornerRadius: radius
            )
            context.fill(fill, with: .color(tint))
        }
        .frame(height: 6)
        .accessibilityLabel("quota remaining")
    }
}
