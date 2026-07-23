import CoreGraphics
import Foundation
import ImageIO

// Icône Home Assistant : cœur détouré sur fond TRANSPARENT (règles brands).
let N = Double(CommandLine.arguments.count > 1 ? Int(CommandLine.arguments[1]) ?? 512 : 512)
let out = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "icon.png"

let cs = CGColorSpace(name: CGColorSpace.sRGB)!
guard let ctx = CGContext(
    data: nil, width: Int(N), height: Int(N), bitsPerComponent: 8,
    bytesPerRow: 0, space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
) else { fatalError("contexte") }

func color(_ r: Double, _ g: Double, _ b: Double, _ a: Double = 1) -> CGColor {
    CGColor(colorSpace: cs, components: [r, g, b, a])!
}

// Cœur cadré pour remplir le carré avec un léger padding (détourage brands).
let heart = N * 1.12
let cx = N / 2
let cy = N * 0.46
let ox = cx - heart / 2
let oy = cy - heart / 2
func p(_ px: Double, _ py: Double) -> CGPoint {
    CGPoint(x: ox + px / 100 * heart, y: oy + (100 - py) / 100 * heart)
}

let path = CGMutablePath()
path.move(to: p(50, 30))
path.addCurve(to: p(30, 15), control1: p(50, 27), control2: p(45, 15))
path.addCurve(to: p(10, 40), control1: p(10, 15), control2: p(10, 40))
path.addCurve(to: p(50, 88), control1: p(10, 60), control2: p(30, 72))
path.addCurve(to: p(90, 40), control1: p(70, 72), control2: p(90, 60))
path.addCurve(to: p(70, 15), control1: p(90, 15), control2: p(90, 15))
path.addCurve(to: p(50, 30), control1: p(55, 15), control2: p(50, 27))
path.closeSubpath()

// Cœur rempli d'un dégradé vert → sarcelle (fond de l'image resté transparent).
let grad = CGGradient(
    colorsSpace: cs,
    colors: [color(0.24, 0.85, 0.52), color(0.02, 0.62, 0.49)] as CFArray,
    locations: [0, 1]
)!
ctx.saveGState()
ctx.addPath(path)
ctx.clip()
ctx.drawLinearGradient(grad, start: CGPoint(x: 0, y: N), end: CGPoint(x: N, y: 0), options: [])
ctx.restoreGState()

// Tracé ECG blanc.
let pulse = CGMutablePath()
let pts = [(20, 50), (37, 50), (44, 34), (52, 70), (60, 42), (67, 50), (80, 50)]
pulse.move(to: p(Double(pts[0].0), Double(pts[0].1)))
for pt in pts.dropFirst() { pulse.addLine(to: p(Double(pt.0), Double(pt.1))) }
ctx.addPath(pulse)
ctx.setStrokeColor(color(1, 1, 1, 1))
ctx.setLineWidth(N * 0.03)
ctx.setLineJoin(.round)
ctx.setLineCap(.round)
ctx.strokePath()

guard let image = ctx.makeImage() else { fatalError("image") }
let url = URL(fileURLWithPath: out)
guard let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil)
else { fatalError("destination") }
CGImageDestinationAddImage(dest, image, nil)
CGImageDestinationFinalize(dest)
print("écrit : \(out) (\(Int(N))px)")
