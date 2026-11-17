"""Draw the KINO « cadre » logo with Quartz: PNGs, .iconset, .ico and SVG.

Usage: .venv_mac/bin/python tools/render_logo.py OUT_DIR, then
  iconutil -c icns OUT_DIR/AppIcon.iconset -o AppIcon.icns ; copy OUT_DIR/kino.png and kino.ico
  to the project root; mark.svg.txt is the header mark (web/index.html, remote_controller.py)
  and favicon.svg the tab icon.
Geometry lives in a 1024 grid (Apple's icon template: a 824 rounded square at 100).
Two drawings: « large » for 64 px and up, « small » (thicker frame, bigger K) below.
"""
import os
import struct
import sys

import Quartz
from Foundation import NSURL

VARIANTS = {
    # frame: x, size, corner radius, stroke; k: scale of the K inside the frame
    "large": {"frame": (216, 592, 140, 46), "k": 0.66},
    "small": {"frame": (176, 672, 150, 76), "k": 0.80},
}
# The K, drawn as three strokes clipped to a horizontal band (flat ends).
K_BAND = (300, 724)
K_STROKES = [((404, 260), (404, 764)), ((404, 610), (760, 202)), ((528, 474), (772, 820))]
K_WIDTH = 104
K_CENTER = (546, 512)


def k_transform(scale):
    """Local K point → 1024 grid point."""
    cx, cy = K_CENTER
    return lambda x, y: (512 + (x - cx) * scale, 512 + (y - cy) * scale)


def draw(ctx, size, variant, background=True):
    spec = VARIANTS[variant]
    Quartz.CGContextSaveGState(ctx)
    Quartz.CGContextTranslateCTM(ctx, 0, size)
    Quartz.CGContextScaleCTM(ctx, size / 1024, -size / 1024)
    rgb = Quartz.CGColorSpaceCreateDeviceRGB()
    if background:
        box = Quartz.CGRectMake(100, 100, 824, 824)
        path = Quartz.CGPathCreateWithRoundedRect(box, 185, 185, None)
        Quartz.CGContextSaveGState(ctx)
        Quartz.CGContextAddPath(ctx, path)
        Quartz.CGContextClip(ctx)
        grad = Quartz.CGGradientCreateWithColorComponents(rgb, [0.114, 0.114, 0.133, 1, 0.039, 0.039, 0.047, 1], [0, 1], 2)
        Quartz.CGContextDrawLinearGradient(ctx, grad, (512, 100), (512, 924), 0)
        Quartz.CGContextRestoreGState(ctx)
        inner = Quartz.CGPathCreateWithRoundedRect(Quartz.CGRectInset(box, 1.5, 1.5), 183.5, 183.5, None)
        Quartz.CGContextAddPath(ctx, inner)
        Quartz.CGContextSetRGBStrokeColor(ctx, 1, 1, 1, 0.10)
        Quartz.CGContextSetLineWidth(ctx, 3)
        Quartz.CGContextStrokePath(ctx)
    x, w, r, stroke = spec["frame"]
    frame = Quartz.CGPathCreateWithRoundedRect(Quartz.CGRectMake(x, x, w, w), r, r, None)
    Quartz.CGContextAddPath(ctx, frame)
    Quartz.CGContextSetRGBStrokeColor(ctx, 0.98, 0.98, 0.98, 1)
    Quartz.CGContextSetLineWidth(ctx, stroke)
    Quartz.CGContextStrokePath(ctx)
    scale = spec["k"]
    t = k_transform(scale)
    Quartz.CGContextSaveGState(ctx)
    (bx0, by0), (bx1, by1) = t(0, K_BAND[0]), t(1024, K_BAND[1])
    Quartz.CGContextClipToRect(ctx, Quartz.CGRectMake(0, by0, 1024, by1 - by0))
    Quartz.CGContextSetLineWidth(ctx, K_WIDTH * scale)
    Quartz.CGContextSetLineCap(ctx, Quartz.kCGLineCapButt)
    for a, b in K_STROKES:
        Quartz.CGContextMoveToPoint(ctx, *t(*a))
        Quartz.CGContextAddLineToPoint(ctx, *t(*b))
    Quartz.CGContextStrokePath(ctx)
    Quartz.CGContextRestoreGState(ctx)
    Quartz.CGContextRestoreGState(ctx)


def png(path, size, variant, background=True):
    rgb = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(None, size, size, 8, size * 4, rgb, Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextSetShouldAntialias(ctx, True)
    Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationHigh)
    draw(ctx, size, variant, background)
    image = Quartz.CGBitmapContextCreateImage(ctx)
    url = NSURL.fileURLWithPath_(path)
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, image, None)
    assert Quartz.CGImageDestinationFinalize(dest), path


def variant_for(pixels):
    return "small" if pixels <= 32 else "large"


def svg(variant, background, clip_id):
    """The same drawing as SVG markup (for the page header and the tab icon)."""
    spec = VARIANTS[variant]
    x, w, r, stroke = spec["frame"]
    scale = spec["k"]
    t = k_transform(scale)
    (_, by0), (_, by1) = t(0, K_BAND[0]), t(1024, K_BAND[1])
    lines = ''.join('M%g %gL%g %g' % (*map(lambda v: round(v, 1), t(*a)), *map(lambda v: round(v, 1), t(*b))) for a, b in K_STROKES)
    parts = []
    if background:
        parts.append('<rect x="100" y="100" width="824" height="824" rx="185" fill="#141417"/>')
    parts.append(f'<rect x="{x}" y="{x}" width="{w}" height="{w}" rx="{r}" fill="none" stroke="#fafafa" stroke-width="{stroke}"/>')
    parts.append(f'<clipPath id="{clip_id}"><rect x="0" y="{round(by0, 1)}" width="1024" height="{round(by1 - by0, 1)}"/></clipPath>')
    parts.append(f'<path d="{lines}" clip-path="url(#{clip_id})" fill="none" stroke="#fafafa" stroke-width="{round(K_WIDTH * scale, 1)}"/>')
    return ''.join(parts)


def ico(path, pngs):
    """Windows .ico holding PNG images (Vista and later read them)."""
    datas = [(size, open(p, 'rb').read()) for size, p in pngs]
    header = struct.pack('<HHH', 0, 1, len(datas))
    offset = 6 + 16 * len(datas)
    entries, blobs = b'', b''
    for size, data in datas:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    with open(path, 'wb') as out:
        out.write(header + entries + blobs)


def main():
    out = sys.argv[1]
    iconset = os.path.join(out, 'AppIcon.iconset')
    os.makedirs(iconset, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        for factor in (1, 2):
            pixels = base * factor
            name = f'icon_{base}x{base}' + ('@2x' if factor == 2 else '') + '.png'
            png(os.path.join(iconset, name), pixels, variant_for(pixels))
    png(os.path.join(out, 'kino.png'), 256, 'large')
    for size in (16, 32, 48, 256):
        png(os.path.join(out, f'ico_{size}.png'), size, variant_for(size))
    ico(os.path.join(out, 'kino.ico'), [(s, os.path.join(out, f'ico_{s}.png')) for s in (16, 32, 48, 256)])
    with open(os.path.join(out, 'mark.svg.txt'), 'w') as f:
        f.write(svg('small', False, 'kinoLogoClip'))
    with open(os.path.join(out, 'favicon.svg'), 'w') as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="100 100 824 824">' + svg('small', True, 'k') + '</svg>')
    print('ok')


if __name__ == '__main__':
    main()
