// Draw the KINO « cadre » logo with Quartz: PNGs, .iconset, .ico and SVG.
//
// Usage: swift tools/render_logo.swift OUT_DIR, then
//   iconutil -c icns OUT_DIR/AppIcon.iconset -o assets/AppIcon.icns ; copy OUT_DIR/kino.png and
//   kino.ico to assets/; mark.svg.txt is the header mark (web/index.html, web/remote.html)
//   and favicon.svg the tab icon.
// Geometry lives in a 1024 grid (Apple's icon template: a 824 rounded square at 100).
// Two drawings: « large » for 64 px and up, « small » (thicker frame, bigger K) below.
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

struct Variant {
    let x: CGFloat, size: CGFloat, radius: CGFloat, stroke: CGFloat, k: CGFloat
}

let variants: [String: Variant] = [
    "large": Variant(x: 216, size: 592, radius: 140, stroke: 46, k: 0.66),
    "small": Variant(x: 176, size: 672, radius: 150, stroke: 76, k: 0.80),
]
// The K, drawn as three strokes clipped to a horizontal band (flat ends).
let kBand: (CGFloat, CGFloat) = (300, 724)
let kStrokes: [((CGFloat, CGFloat), (CGFloat, CGFloat))] = [((404, 260), (404, 764)), ((404, 610), (760, 202)), ((528, 474), (772, 820))]
let kWidth: CGFloat = 104
let kCenter: (CGFloat, CGFloat) = (546, 512)

/// Local K point → 1024 grid point.
func kPoint(_ x: CGFloat, _ y: CGFloat, _ scale: CGFloat) -> CGPoint {
    CGPoint(x: 512 + (x - kCenter.0) * scale, y: 512 + (y - kCenter.1) * scale)
}

func draw(_ ctx: CGContext, size: CGFloat, variant: String, background: Bool) {
    let spec = variants[variant]!
    ctx.saveGState()
    ctx.translateBy(x: 0, y: size)
    ctx.scaleBy(x: size / 1024, y: -size / 1024)
    let rgb = CGColorSpaceCreateDeviceRGB()
    if background {
        let box = CGRect(x: 100, y: 100, width: 824, height: 824)
        ctx.saveGState()
        ctx.addPath(CGPath(roundedRect: box, cornerWidth: 185, cornerHeight: 185, transform: nil))
        ctx.clip()
        let gradient = CGGradient(colorSpace: rgb, colorComponents: [0.114, 0.114, 0.133, 1, 0.039, 0.039, 0.047, 1], locations: [0, 1], count: 2)!
        ctx.drawLinearGradient(gradient, start: CGPoint(x: 512, y: 100), end: CGPoint(x: 512, y: 924), options: [])
        ctx.restoreGState()
        ctx.addPath(CGPath(roundedRect: box.insetBy(dx: 1.5, dy: 1.5), cornerWidth: 183.5, cornerHeight: 183.5, transform: nil))
        ctx.setStrokeColor(red: 1, green: 1, blue: 1, alpha: 0.10)
        ctx.setLineWidth(3)
        ctx.strokePath()
    }
    let frame = CGRect(x: spec.x, y: spec.x, width: spec.size, height: spec.size)
    ctx.addPath(CGPath(roundedRect: frame, cornerWidth: spec.radius, cornerHeight: spec.radius, transform: nil))
    ctx.setStrokeColor(red: 0.98, green: 0.98, blue: 0.98, alpha: 1)
    ctx.setLineWidth(spec.stroke)
    ctx.strokePath()
    ctx.saveGState()
    let top = kPoint(0, kBand.0, spec.k).y
    let bottom = kPoint(1024, kBand.1, spec.k).y
    ctx.clip(to: CGRect(x: 0, y: top, width: 1024, height: bottom - top))
    ctx.setLineWidth(kWidth * spec.k)
    ctx.setLineCap(.butt)
    for (a, b) in kStrokes {
        ctx.move(to: kPoint(a.0, a.1, spec.k))
        ctx.addLine(to: kPoint(b.0, b.1, spec.k))
    }
    ctx.strokePath()
    ctx.restoreGState()
    ctx.restoreGState()
}

func png(_ path: String, _ pixels: Int, _ variant: String, background: Bool = true) {
    let rgb = CGColorSpace(name: CGColorSpace.sRGB)!
    let ctx = CGContext(data: nil, width: pixels, height: pixels, bitsPerComponent: 8, bytesPerRow: pixels * 4, space: rgb,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.setShouldAntialias(true)
    ctx.interpolationQuality = .high
    draw(ctx, size: CGFloat(pixels), variant: variant, background: background)
    let image = ctx.makeImage()!
    let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: path) as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, image, nil)
    precondition(CGImageDestinationFinalize(dest), path)
}

func variantFor(_ pixels: Int) -> String { pixels <= 32 ? "small" : "large" }

/// As Python's "%g" % round(v, 1).
func g(_ v: CGFloat) -> String {
    let r = (v * 10).rounded() / 10
    return r == r.rounded() ? String(Int(r)) : String(format: "%.1f", r)
}

/// The same drawing as SVG markup (for the page header and the tab icon).
func svg(_ variant: String, background: Bool, clipId: String) -> String {
    let spec = variants[variant]!
    let top = kPoint(0, kBand.0, spec.k).y
    let bottom = kPoint(1024, kBand.1, spec.k).y
    let lines = kStrokes.map { a, b -> String in
        let p = kPoint(a.0, a.1, spec.k), q = kPoint(b.0, b.1, spec.k)
        return "M\(g(p.x)) \(g(p.y))L\(g(q.x)) \(g(q.y))"
    }.joined()
    var parts: [String] = []
    if background {
        parts.append("<rect x=\"100\" y=\"100\" width=\"824\" height=\"824\" rx=\"185\" fill=\"#141417\"/>")
    }
    parts.append("<rect x=\"\(g(spec.x))\" y=\"\(g(spec.x))\" width=\"\(g(spec.size))\" height=\"\(g(spec.size))\" rx=\"\(g(spec.radius))\" fill=\"none\" stroke=\"#fafafa\" stroke-width=\"\(g(spec.stroke))\"/>")
    parts.append("<clipPath id=\"\(clipId)\"><rect x=\"0\" y=\"\(g(top))\" width=\"1024\" height=\"\(g(bottom - top))\"/></clipPath>")
    parts.append("<path d=\"\(lines)\" clip-path=\"url(#\(clipId))\" fill=\"none\" stroke=\"#fafafa\" stroke-width=\"\(g(kWidth * spec.k))\"/>")
    return parts.joined()
}

/// Windows .ico holding PNG images (Vista and later read them).
func ico(_ path: String, _ images: [(Int, String)]) throws {
    let datas = try images.map { ($0.0, try Data(contentsOf: URL(fileURLWithPath: $0.1))) }
    var out = Data()
    func le16(_ v: Int) { out.append(contentsOf: [UInt8(v & 0xff), UInt8((v >> 8) & 0xff)]) }
    func le32(_ v: Int) { le16(v & 0xffff); le16((v >> 16) & 0xffff) }
    le16(0); le16(1); le16(datas.count)
    var offset = 6 + 16 * datas.count
    for (size, data) in datas {
        out.append(contentsOf: [UInt8(size % 256), UInt8(size % 256), 0, 0])
        le16(1); le16(32); le32(data.count); le32(offset)
        offset += data.count
    }
    for (_, data) in datas { out.append(data) }
    try out.write(to: URL(fileURLWithPath: path))
}

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."
let iconset = (out as NSString).appendingPathComponent("AppIcon.iconset")
try FileManager.default.createDirectory(atPath: iconset, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    for factor in [1, 2] {
        let pixels = base * factor
        png((iconset as NSString).appendingPathComponent("icon_\(base)x\(base)" + (factor == 2 ? "@2x" : "") + ".png"), pixels, variantFor(pixels))
    }
}
png((out as NSString).appendingPathComponent("kino.png"), 256, "large")
for size in [16, 32, 48, 256] {
    png((out as NSString).appendingPathComponent("ico_\(size).png"), size, variantFor(size))
}
try ico((out as NSString).appendingPathComponent("kino.ico"), [16, 32, 48, 256].map { ($0, (out as NSString).appendingPathComponent("ico_\($0).png")) })
try svg("small", background: false, clipId: "kinoLogoClip").write(toFile: (out as NSString).appendingPathComponent("mark.svg.txt"), atomically: true, encoding: .utf8)
try ("<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"100 100 824 824\">" + svg("small", background: true, clipId: "k") + "</svg>")
    .write(toFile: (out as NSString).appendingPathComponent("favicon.svg"), atomically: true, encoding: .utf8)
print("ok")
