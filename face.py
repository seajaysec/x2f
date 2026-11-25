"""x2f face: an Open-XY machine's own screen (display.cpp over panel.svg.fragment) on open-field's page.

The OP-XY renders a retained frame: nodes (place, Q10 scale, Q10 turn, palette shade, paint) over the panel's
shapes, dynamic paths, text slots and a four-entry header. open-field gives a pack a raster page and line/box/text
painters, so x2f parses the panel's shapes here and rasterises the frame on the display thread (face template).

Colour rule (op1f-design): Field encoder colours are reserved for the parts each encoder changes. OP-XY panels are
greyscale and the art does not say who owns what, so ownership is measured: the probe runs the machine's display()
on the host and turns each page control; a node that one control alone moves or reshapes takes that control's
colour (blue #3C6087, tan #8A653C, gray #CDD0C2, orange #E52817). White and near-white stay white (playback);
everything else keeps its own grey.
"""
import math, os, re, subprocess

HEADER_BAND = 38      # the OP-XY's two-row page header (names, values) above this y: not drawn, the lanes show them
FIELD_ENCODERS = (0x3C6087, 0x8A653C, 0xCDD0C2, 0xE52817)


def _colour(text):
    if not text or text == "none":
        return None
    m = re.match(r"#([0-9a-fA-F]{6})", text.strip())
    return int(m.group(1), 16) if m else None


def _attr(tag, name):
    m = re.search(r'\b%s="([^"]*)"' % name, tag)
    return m.group(1) if m else None


def _matrix(tag):
    m = re.search(r"matrix\(\s*([-\d.e]+)[ ,]+([-\d.e]+)[ ,]+([-\d.e]+)[ ,]+([-\d.e]+)[ ,]+([-\d.e]+)[ ,]+([-\d.e]+)", tag or "")
    return tuple(float(v) for v in m.groups()) if m else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _translate(tag):
    m = re.search(r"matrix\(\s*([-\d.e]+)[ ,]+([-\d.e]+)[ ,]+([-\d.e]+)[ ,]+([-\d.e]+)[ ,]+([-\d.e]+)[ ,]+([-\d.e]+)", tag or "")
    return (float(m.group(5)), float(m.group(6))) if m else (0.0, 0.0)


def _path_points(d):
    """An SVG path's outlines as polylines, every command absolute or relative (M L H V C S Q T A Z): a curve is
    flattened to 8 segments, an arc to 12. Returns [(points, closed)]. After Z the pen is back at the outline's
    start, as SVG says, so a relative move after it starts from there."""
    out, cur = [], []
    x = y = sx = sy = 0.0
    lcx = lcy = None                                   # the last control point, for S and T
    toks = re.findall(r"[MLHVCSQTAZmlhvcsqtaz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d)
    i, cmd = 0, None
    def num():
        nonlocal i
        v = float(toks[i]); i += 1; return v
    def cubic(x0, y0, x1, y1, x2, y2, x3, y3):
        for k in range(1, 9):
            t = k / 8; u = 1 - t
            cur.append((u**3*x0 + 3*u*u*t*x1 + 3*u*t*t*x2 + t**3*x3, u**3*y0 + 3*u*u*t*y1 + 3*u*t*t*y2 + t**3*y3))
    while i < len(toks):
        if re.match(r"[A-Za-z]", toks[i]):
            cmd = toks[i]; i += 1
            if cmd in "Zz":
                if cur: out.append((cur, True)); cur = []
                x, y = sx, sy; lcx = lcy = None
                continue
        elif cmd is None:
            i += 1; continue
        rel = cmd.islower(); C = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if C == "M":
            if cur: out.append((cur, False))
            x, y = ox + num(), oy + num(); sx, sy = x, y; cur = [(x, y)]
            cmd = "l" if rel else "L"; lcx = lcy = None
        elif C == "L":
            x, y = ox + num(), oy + num(); cur.append((x, y)); lcx = lcy = None
        elif C == "H":
            x = (x if rel else 0.0) + num(); cur.append((x, y)); lcx = lcy = None
        elif C == "V":
            y = (y if rel else 0.0) + num(); cur.append((x, y)); lcx = lcy = None
        elif C in "CS":
            if C == "C":
                x1, y1 = ox + num(), oy + num()
            else:
                x1, y1 = (2 * x - lcx, 2 * y - lcy) if lcx is not None else (x, y)
            x2, y2, x3, y3 = ox + num(), oy + num(), ox + num(), oy + num()
            if not cur: cur = [(x, y)]
            cubic(x, y, x1, y1, x2, y2, x3, y3); lcx, lcy = x2, y2; x, y = x3, y3
        elif C in "QT":
            if C == "Q":
                qx, qy = ox + num(), oy + num()
            else:
                qx, qy = (2 * x - lcx, 2 * y - lcy) if lcx is not None else (x, y)
            x3, y3 = ox + num(), oy + num()
            if not cur: cur = [(x, y)]
            cubic(x, y, x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y), x3 + 2 / 3 * (qx - x3), y3 + 2 / 3 * (qy - y3), x3, y3)
            lcx, lcy = qx, qy; x, y = x3, y3
        elif C == "A":
            rx, ry, rot, large, sweep = abs(num()), abs(num()), num(), num(), num()
            x3, y3 = ox + num(), oy + num()
            if not cur: cur = [(x, y)]
            cur.extend(_arc(x, y, rx, ry, rot, large != 0, sweep != 0, x3, y3))
            x, y = x3, y3; lcx = lcy = None
    if cur:
        out.append((cur, False))
    return out


def _arc(x0, y0, rx, ry, rot, large, sweep, x1, y1):
    """An SVG arc (endpoint form) as 12 points ending at (x1, y1)."""
    if rx == 0 or ry == 0 or (x0 == x1 and y0 == y1):
        return [(x1, y1)]
    c, s_ = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    dx, dy = (x0 - x1) / 2, (y0 - y1) / 2
    x_, y_ = c * dx + s_ * dy, -s_ * dx + c * dy
    lam = x_ * x_ / (rx * rx) + y_ * y_ / (ry * ry)
    if lam > 1:
        rx *= math.sqrt(lam); ry *= math.sqrt(lam)
    num_ = rx * rx * ry * ry - rx * rx * y_ * y_ - ry * ry * x_ * x_
    k = math.sqrt(max(0.0, num_ / (rx * rx * y_ * y_ + ry * ry * x_ * x_)))
    if large == sweep:
        k = -k
    cx_, cy_ = k * rx * y_ / ry, -k * ry * x_ / rx
    cx, cy = c * cx_ - s_ * cy_ + (x0 + x1) / 2, s_ * cx_ + c * cy_ + (y0 + y1) / 2
    a0 = math.atan2((y_ - cy_) / ry, (x_ - cx_) / rx)
    a1 = math.atan2((-y_ - cy_) / ry, (-x_ - cx_) / rx)
    da = a1 - a0
    if sweep and da < 0: da += 2 * math.pi
    if not sweep and da > 0: da -= 2 * math.pi
    pts = []
    for n in range(1, 13):
        a = a0 + da * n / 12
        ex, ey = rx * math.cos(a), ry * math.sin(a)
        pts.append((c * ex - s_ * ey + cx, s_ * ex + c * ey + cy))
    pts[-1] = (x1, y1)
    return pts


def _shapes(tag, kind):
    if kind == "path":
        return _path_points(_attr(tag, "d") or "")
    if kind in ("circle", "ellipse"):
        cx, cy = float(_attr(tag, "cx") or 0), float(_attr(tag, "cy") or 0)
        rx = float(_attr(tag, "r") or _attr(tag, "rx") or 0); ry = float(_attr(tag, "r") or _attr(tag, "ry") or 0)
        n = 8 if max(rx, ry) < 4 else 16
        return [([(cx + rx * math.cos(2 * math.pi * k / n), cy + ry * math.sin(2 * math.pi * k / n)) for k in range(n)], True)]
    if kind == "rect":
        x0, y0 = float(_attr(tag, "x") or 0), float(_attr(tag, "y") or 0)
        w, h = float(_attr(tag, "width") or 0), float(_attr(tag, "height") or 0)
        return [([(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)], True)]
    pts = [float(v) for v in re.findall(r"-?[\d.]+(?:e-?\d+)?", _attr(tag, "points") or "")]
    return [(list(zip(pts[0::2], pts[1::2])), kind == "polygon")]


def parse_panel(path):
    """The panel in document order: moving nodes (id *_node_N), palette entries, and the fixed artwork and text
    the OP-XY draws as it is (everything else, transformed to screen pixels here; parked shapes off screen are
    dropped). Text slots (*_text_N) are the machine's to fill and are not drawn from the panel."""
    text = open(path).read()
    nodes, palette, statics, order, text_sizes, text_colours = {}, {}, [], [], {}, {}
    for el in re.finditer(r"<(path|circle|rect|ellipse|polygon|polyline|text)\b([^>]*)>(?:([^<]*)</text>)?", text):
        kind, tag, words = el.group(1), el.group(0), el.group(3)
        ident = _attr(tag, "id") or ""
        fill, stroke = _colour(_attr(tag, "fill")), _colour(_attr(tag, "stroke"))
        width = float(_attr(tag, "stroke-width") or 1)
        op = 1.0
        for key in ("opacity", "fill-opacity"):
            if _attr(tag, key):
                try: op *= float(_attr(tag, key))
                except ValueError: pass
        alpha = max(0, min(255, int(round(op * 255))))
        if _attr(tag, "display") == "none" or _attr(tag, "visibility") == "hidden":
            continue
        pm = re.search(r"_palette_(\d+)$", ident)
        if pm:
            palette[int(pm.group(1))] = (fill, stroke, width, alpha)
            continue
        tm = re.search(r"_text_(\d+)$", ident)
        if tm:                                        # a text slot: the machine fills it; its size is the panel's
            size = float(re.sub(r"[^\d.]", "", _attr(tag, "font-size") or "12") or 12)
            text_sizes[int(tm.group(1))] = size
            text_colours[int(tm.group(1))] = fill if fill is not None else 0xFFFFFF
            continue
        a, b, c, d, e, f = _matrix(_attr(tag, "transform"))
        if kind == "text":
            if not words or not words.strip():
                continue
            x, y = float(_attr(tag, "x") or 0), float(_attr(tag, "y") or 0)
            X, Y = a * x + c * y + e, b * x + d * y + f
            size = float(re.sub(r"[^\d.]", "", _attr(tag, "font-size") or "12") or 12)
            anchor = {"middle": 1, "end": 2}.get(_attr(tag, "text-anchor") or "", 0)
            if 0 <= X < 480 and 0 <= Y < 230:
                statics.append({"text": words.strip()[:23], "x": X, "y": Y, "size": size, "anchor": anchor,
                                "fill": fill if fill is not None else 0xFFFFFF})
                order.append(("s", len(statics) - 1))
            continue
        nm = re.search(r"_node_(\d+)$", ident)
        shapes = _shapes(tag, kind)
        if nm:
            nodes[int(nm.group(1))] = {"shapes": shapes, "fill": fill, "stroke": stroke, "width": width, "at": (e, f), "alpha": alpha}
            order.append(("n", int(nm.group(1))))
            continue
        placed = [([(a * x + c * y + e, b * x + d * y + f) for x, y in pts], closed) for pts, closed in shapes]
        if not any(-64 < x < 544 and -64 < y < 284 for pts, _ in placed for x, y in pts):
            continue                                  # parked off screen
        if fill is None and stroke is None and kind == "path":
            fill = 0                                  # SVG's default fill is black
        statics.append({"shapes": placed, "fill": fill, "stroke": stroke, "width": width * max(abs(a), abs(d), 1e-9), "alpha": alpha})
        order.append(("s", len(statics) - 1))
    # A machine that draws its own page header (hurdygurdy: "it covers the stock header, which this machine draws
    # itself") has artwork in the band above HEADER_BAND; for the others x2f draws the header the OP-XY would.
    def in_band(pts):
        return pts and all(-1 <= y <= HEADER_BAND for _, y in pts) and any(0 <= x < 480 for x, _ in pts)
    own = any(in_band(pts) for st in statics if "shapes" in st for pts, _ in st["shapes"]) or \
        any(in_band([(x + n["at"][0], y + n["at"][1]) for x, y in pts]) for n in nodes.values() for pts, _ in n["shapes"]
            if n["at"][1] < HEADER_BAND)
    return {"nodes": nodes, "palette": palette, "statics": statics, "order": order, "own_header": own,
            "text_sizes": text_sizes, "text_colours": text_colours}


def tables(panel, owners, node_count, palette_count):
    """C++ tables for the face template: points (Q10 offsets), shapes, nodes' own colours and owners, palette."""
    points, shapes, rows = [], [], []
    for n in range(node_count):
        node = panel["nodes"].get(n, {"shapes": [], "fill": None, "stroke": None, "width": 1, "at": (640, 320), "alpha": 255})
        first = len(shapes)
        for pts, closed in node["shapes"]:
            shapes.append("{%d, %d, %d}" % (len(points), len(pts), 1 if closed else 0))
            points += ["{%d, %d}" % (round(x * 1024), round(y * 1024)) for x, y in pts]
        rgb = lambda c: -1 if c is None else c
        rows.append("{%d, %d, %d, %d, %d, %d, %d, %d, %d}" % (first, len(shapes) - first, rgb(node["fill"]), rgb(node["stroke"]),
                    max(1, round(node["width"] * 16)), round(node["at"][0] * 1024), round(node["at"][1] * 1024),
                    owners.get(n, -1), node.get("alpha", 255)))
    stat_rows, stat_text = [], []
    for st in panel["statics"]:
        if "text" in st:
            stat_rows.append("{-1, %d, %d, %d, %d, %d, %d, %d, 255}" % (len(stat_text), st["fill"], 0, int(round(st["size"])),
                             round(st["x"]), round(st["y"]), st["anchor"]))
            stat_text.append(c_text(st["text"]))
            continue
        first = len(shapes)
        for pts, closed in st["shapes"]:
            shapes.append("{%d, %d, %d}" % (len(points), len(pts), 1 if closed else 0))
            points += ["{%d, %d}" % (round(x * 1024), round(y * 1024)) for x, y in pts]
        stat_rows.append("{%d, %d, %d, %d, %d, 0, 0, 0, %d}" % (first, len(shapes) - first,
                         -1 if st["fill"] is None else st["fill"], -1 if st["stroke"] is None else st["stroke"],
                         max(1, round(st["width"] * 16)), st.get("alpha", 255)))
    order = ["{%d, %d}" % (1 if k == "n" else 0, i) for k, i in panel["order"] if k == "s" or i < node_count]
    seen = {i for k, i in panel["order"] if k == "n"}
    order += ["{1, %d}" % n for n in range(node_count) if n not in seen]
    pal = []
    for k in range(max(1, palette_count)):
        f, s_, w, al = panel["palette"].get(k, (None, 0xFFFFFF, 1, 255))
        pal.append("{%d, %d, %d, %d}" % (-1 if f is None else f, 0xFFFFFF if s_ is None else s_, max(1, round(w * 16)), al))
    return {"FPOINTS": ", ".join(points) or "{0, 0}", "FSHAPES": ", ".join(shapes) or "{0, 0, 0}",
            "FNODES": ", ".join(rows), "FPALETTE": ", ".join(pal), "FSTATIC": ", ".join(stat_rows) or "{0, 0, -1, -1, 1, 0, 0, 0, 255}",
            "FSTATIC_TEXT": ", ".join(stat_text) or '""', "FORDER": ", ".join(order) or "{0, 0}",
            "NSTATIC": len(stat_rows), "NORDER": len(order), "OWN_HEADER": 1 if panel.get("own_header") else 0,
            # each text slot's font size in pixels, from the panel
            "TEXT_COLOURS": ", ".join(str(panel["text_colours"].get(i, 0xFFFFFF))
                                      for i in range(max(panel["text_sizes"]) + 1 if panel["text_sizes"] else 1)),
            "TEXT_SIZES": ", ".join(str(int(round(panel["text_sizes"].get(i, 12))))
                                     for i in range(max(panel["text_sizes"]) + 1 if panel["text_sizes"] else 1))}


def c_text(t):
    """Panel text for open-field's painter: lower case, characters it draws, C-escaped."""
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"')
    t = "".join(ch for ch in t if 32 <= ord(ch) < 127)
    return '"%s"' % t.replace("\\", "\\\\").replace('"', '\\"')


FACE = r'''// Generated by x2f from the Open-XY machine @ID@'s screen (display.cpp, panel.svg.fragment). Do not edit.
// The machine's own display() runs on the display thread each frame; this file rasterises its retained frame
// onto open-field's page (face.py says how, and the colour rule).
#define X2F_NO_LIBCALLS
#include "x2f_card.h"
#include "x2f_rt.h"
#include "x2f_text.h"
#include "@DISPLAY@"

extern "C" const Op1fCard @CARD@ OP1F_NAMED("@CARD@");
extern "C" volatile unsigned @P@_note_on OP1F_NAMED("@P@_note_on");
extern "C" volatile unsigned @P@_note_off OP1F_NAMED("@P@_note_off");
extern "C" volatile unsigned char @P@_held[128] OP1F_NAMED("@P@_held");
volatile unsigned @P@_note_on, @P@_note_off;
volatile unsigned char @P@_held[128];

namespace {
enum { X2F_VALUES = @NV@ };
const int32_t X2F_ENUM[X2F_VALUES][@EN@] = { @ENUMS@ };
const unsigned char X2F_ENUM_N[X2F_VALUES] = { @ENUM_NS@ };   // a choice's options (its knob's travel in as many zones)
inline unsigned x2f_zone(int v, unsigned n) { if (v <= 0 || n < 2) return 0; const unsigned z = ((unsigned)v * n) >> 15; return z >= n ? n - 1 : z; }
const char X2F_NAMES[X2F_VALUES][16] = { @NAMES@ };
const char X2F_LABELS[X2F_VALUES][@EN@][16] = { @LABELS@ };
const int32_t X2F_FIXED[@NP@] = { @FIXED@ };
const unsigned X2F_MAP[X2F_VALUES] = { @MAP@ };
const unsigned X2F_ENCODER[4] = {OP1F_COLOUR(0x3c, 0x60, 0x87), OP1F_COLOUR(0x8a, 0x65, 0x3c),
                                 OP1F_COLOUR(0xcd, 0xd0, 0xc2), OP1F_COLOUR(0xe5, 0x28, 0x17)};
const unsigned X2F_NAME_GREY = OP1F_COLOUR(0xa2, 0xa4, 0xa6);
struct FPoint { int32_t x, y; };
struct FShape { uint16_t first, count, closed; };
struct FNode { int16_t shape, shapes; int32_t fill, stroke, width, x, y, owner, alpha; };
struct FPal { int32_t fill, stroke, width, alpha; };     // alpha: the SVG's opacity, 0..255
const FPoint F_POINTS[] = { @FPOINTS@ };
const FShape F_SHAPES[] = { @FSHAPES@ };
const FNode F_NODES[] = { @FNODES@ };
const FPal F_PALETTE[] = { @FPALETTE@ };
const unsigned char F_TEXT_SIZE[] = { @TEXT_SIZES@ };       // each text slot's font size, from the panel
const int32_t F_TEXT_COLOUR[] = { @TEXT_COLOURS@ };          // ... and its colour (dark on light cells, light on dark)
// Fixed artwork in the panel, in screen pixels (shape >= 0), or fixed text (shape -1: shapes = text index,
// stroke = scale, x, y = its baseline start, owner = anchor 0 start / 1 middle / 2 end).
const FNode F_STATIC[] = { @FSTATIC@ };
const char* const F_STATIC_TEXT[] = { @FSTATIC_TEXT@ };
struct FOrder { uint16_t node, index; };              // the panel's document order: node 1 = a moving node
const FOrder F_ORDER[] = { @FORDER@ };
enum { F_NSTATIC = @NSTATIC@, F_NORDER = @NORDER@ };

using FrameT = @NS@::Frame;
FrameT frame;
@NS@::DisplayState state;
opxy::draw::Input input;
unsigned started, last_ms, stood, said, x2f_shift_held;
// Screen colours, the pack's option 0 (own, partial, full), kept by the base; the gesture steps it: all four knobs
// held down, then shift. The input function only counts; the frame function sets the option (op1f_option_set is
// the frame function's to call).
unsigned x2f_mode = @MODE@, x2f_flips_seen, x2f_said_at;
volatile unsigned x2f_knobs_held, x2f_flips;
volatile unsigned where_, how_;

inline int32_t amount(int v) { return v <= 0 ? 0 : v >= 32767 ? 0x7fffffff : v * 65538; }

unsigned colour565(int32_t rgb) { return OP1F_COLOUR((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255); }
bool greyish(int32_t rgb) {                                   // r, g and b within 12 of each other
    const int r = (rgb >> 16) & 255, g = (rgb >> 8) & 255, b = rgb & 255;
    const int hi = r > g ? (r > b ? r : b) : (g > b ? g : b), lo = r < g ? (r < b ? r : b) : (g < b ? g : b);
    return rgb >= 0 && hi - lo <= 12;
}
unsigned char header_light[2][120];                   // the header's two rows, by four pixels: the cell's lightness
int luma(int32_t rgb) { return (((rgb >> 16) & 255) * 3 + ((rgb >> 8) & 255) * 6 + (rgb & 255)) / 10; }
int luma_under(int x, int y) { x += 6; return x >= 0 && x < 480 ? header_light[y >= 20 ? 1 : 0][x >> 2] : 0; }

// A grey recoloured as an encoder's colour at the grey's own brightness: light greys light, dark greys dark, so
// the machine's shading (depth, layers, highlights) survives the colour.
int32_t tint(int32_t grey, int32_t encoder) {
    // A smooth ramp through five anchor shades (dark, deep, mid, the colour itself, lifted toward white), so two
    // different greys (a train's top and its side) always keep two different hues, and the spread stays wide.
    // A lightness below 0 scales the colour down (256 + l of 256); above 0 lifts it toward white by l percent.
    // The flipped looks run the anchors the other way inside the light zone (never darker than the mid shade).
    const int32_t g = grey & 255;
    static const int16_t at[5] = {0x18, 0x48, 0x78, 0xAC, 0xE4};
    static const int16_t normal[5] = {-186, -136, -81, 0, 40}, flipped[5] = {55, 30, 0, -66, -116};
    const int16_t* l = x2f_mode >= 3u ? flipped : normal;
    int light;
    if (g <= at[0]) light = l[0];
    else if (g >= at[4]) light = l[4];
    else { int i = 0; while (g > at[i + 1]) ++i; light = l[i] + (l[i + 1] - l[i]) * (g - at[i]) / (at[i + 1] - at[i]); }
    int32_t r = (encoder >> 16) & 255, gg = (encoder >> 8) & 255, b = encoder & 255;
    if (light < 0) { const int k = 256 + light; r = r * k >> 8; gg = gg * k >> 8; b = b * k >> 8; }
    else { r += (255 - r) * light / 100; gg += (255 - gg) * light / 100; b += (255 - b) * light / 100; }
    return (r << 16) | (gg << 8) | b;
}
bool whiteish(int32_t rgb) { return rgb >= 0 && ((rgb >> 16) & 255) > 0xe0 && ((rgb >> 8) & 255) > 0xe0 && (rgb & 255) > 0xe0; }

// Shapes are drawn anti-aliased at a sixteenth of a pixel, as the OP-XY's renderer draws them: a shape is all the
// outlines of one SVG element, filled together by the even-odd rule (an outline inside another is a hole), its
// coverage worked out on four sub-rows a pixel and painted through coverage masks; a stroke is each segment drawn
// as a quad of its true width (widths are kept in sixteenths: the panels' 0.5 px lines stay half a pixel).
enum { SHAPE_POINTS = 4096, SHAPE_OUTLINES = 256, Q = 16 };
inline int16_t q16(int32_t v) { return (int16_t)(v < -32000 ? -32000 : v > 32000 ? 32000 : v); }
enum { EDGES = SHAPE_POINTS + 64 };
struct Edge { int16_t first, last; int32_t x, step; int8_t dir; };   // sub-rows first..last; x and step in 1/4096 px
// The drawing's working memory (~105 KB) comes from the pack's memory ask, out of the unit's pool: a pack's own
// zeroed memory counts against the loader's 2 MB of room, which these buffers had filled.
struct Scratch {
    int16_t sx[SHAPE_POINTS], sy[SHAPE_POINTS];        // sixteenths of a pixel, kept to +-2000 px
    unsigned s_first[SHAPE_OUTLINES], s_count[SHAPE_OUTLINES], s_closed[SHAPE_OUTLINES];
    Edge edges[EDGES];
    unsigned short edge_order[EDGES], active[EDGES];
    int32_t xs[EDGES];
    int8_t ds[EDGES];
};
static_assert(sizeof(Scratch) <= 131072, "the screen's memory ask holds the Scratch");
Scratch* scr;
#define sx (scr->sx)
#define sy (scr->sy)
#define s_first (scr->s_first)
#define s_count (scr->s_count)
#define s_closed (scr->s_closed)
#define edges (scr->edges)
#define edge_order (scr->edge_order)
unsigned s_points, s_outlines;

void shape_begin() { s_points = 0; s_outlines = 0; }
void shape_add(const opxy::draw::Node& node, const FPoint* pts, unsigned n, bool closed) {
    if (s_outlines >= SHAPE_OUTLINES || !n) return;
    if (n > SHAPE_POINTS - s_points) n = SHAPE_POINTS - s_points;
    s_first[s_outlines] = s_points; s_count[s_outlines] = n; s_closed[s_outlines] = closed;
    for (unsigned i = 0; i < n; ++i) {
        const int32_t px = pts[i].x, py = pts[i].y;
        const int32_t rx = ((px >> 2) * node.cos - (py >> 2) * node.sin) >> 8;   // Q10 turn, kept inside 32 bits
        const int32_t ry = ((px >> 2) * node.sin + (py >> 2) * node.cos) >> 8;
        sx[s_points + i] = q16((node.x + (((rx >> 2) * node.scale) >> 8)) >> 6);    // Q10 -> sixteenths
        sy[s_points + i] = q16((node.y + (((ry >> 2) * node.scale) >> 8)) >> 6);
    }
    s_points += n; ++s_outlines;
}
int shape_alpha = 255;
unsigned short cov[481];
unsigned char mask_row[480];
// One pixel row of coverage (0..256 a pixel) painted: full runs as boxes, edges through the mask.
void cover_row(void* canvas, int y, int lo, int hi, unsigned colour) {
    if (lo < 0) lo = 0;
    if (hi > 479) hi = 479;
    int x = lo;
    while (x <= hi) {
        if (!cov[x]) { ++x; continue; }
        int e = x;
        if (cov[x] >= 256 && shape_alpha >= 250) {
            while (e <= hi && cov[e] >= 256) ++e;
            op1f_paint_box(canvas, x, y, e - x, 1, colour);
        } else {
            while (e <= hi && cov[e] && !(cov[e] >= 256 && shape_alpha >= 250)) {
                const int c = cov[e] >= 256 ? 255 : cov[e];
                mask_row[e - x] = (unsigned char)((c * shape_alpha * 257) >> 16);   // no divide a pixel
                ++e;
            }
            op1f_paint_mask(canvas, x, y, e - x, 1, mask_row, e - x, colour);
        }
        x = e;
    }
}
// The fill: an edge table, the cost a frame bounded by edges and rows, with no division in the inner loop. Each
// edge is set up once (one 32-bit divide: its slope), then stepped by an add for every sub-row it crosses. Edges
// carry their direction, so a shape fills by the even-odd rule (SVG's for these panels) and a stroke, all its
// segments' quads together, by the non-zero rule (overlapping quads at the joints do not cancel).
unsigned n_edges;
void edges_begin() { n_edges = 0; }
void edge_add(int x0, int y0, int x1, int y1) {          // sixteenths of a pixel
    if (y0 == y1 || n_edges >= EDGES) return;
    int8_t dir = 1;
    if (y0 > y1) { int t = x0; x0 = x1; x1 = t; t = y0; y0 = y1; y1 = t; dir = -1; }
    // Sub-row k samples y = 4k + 2 (sixteenths): the edge covers the samples in [y0, y1).
    const int first = (y0 - 2 + 3) >> 2, last = ((y1 - 2 + 3) >> 2) - 1;   // floor((y-2+3)/4) = ceil((y-2)/4)
    if (last < first || last < 0 || first > 220 * 4) return;
    const int32_t slope = ((int32_t)(x1 - x0) << 8) / (y1 - y0);         // 1/4096 px per sixteenth of y
    Edge& e = edges[n_edges++];
    e.first = (int16_t)first; e.last = (int16_t)last; e.dir = dir;
    e.x = ((int32_t)x0 << 8) + slope * (first * 4 + 2 - y0);
    e.step = slope * 4;
}
void edges_fill(void* canvas, bool nonzero, unsigned colour) {
    if (!n_edges) return;
    for (unsigned i = 0; i < n_edges; ++i) edge_order[i] = (unsigned short)i;
    for (unsigned a = 1; a < n_edges; ++a) {               // by first sub-row
        const unsigned short v = edge_order[a]; unsigned b = a;
        while (b && edges[edge_order[b - 1]].first > edges[v].first) { edge_order[b] = edge_order[b - 1]; --b; }
        edge_order[b] = v;
    }
    int k = edges[edge_order[0]].first, kmax = 0;
    if (k < 0) k = 0;                                       // rows above the screen are skipped, not stepped
    for (unsigned i = 0; i < n_edges; ++i) if (edges[i].last > kmax) kmax = edges[i].last;
    if (kmax > 220 * 4 - 1) kmax = 220 * 4 - 1;
    unsigned short* const active = scr->active;
    unsigned n_active = 0, next = 0;
    int lo = 480, hi = -1;
    for (; k <= kmax; ++k) {
        while (next < n_edges && edges[edge_order[next]].first <= k) {
            Edge& e = edges[edge_order[next]];
            if (e.first < k) e.x += e.step * (k - e.first);   // an edge from above the screen, caught up at once
            active[n_active++] = edge_order[next++];
        }
        unsigned w = 0;
        for (unsigned i = 0; i < n_active; ++i) {          // drop finished edges, catch late starters up
            Edge& e = edges[active[i]];
            if (e.last < k) continue;
            active[w++] = active[i];
        }
        n_active = w;
        if (k >= 0 && n_active >= 2) {
            int32_t* const xs = scr->xs; int8_t* const ds = scr->ds;
            for (unsigned i = 0; i < n_active; ++i) {
                const Edge& e = edges[active[i]];
                int32_t x = e.x >> 8; unsigned b = i; const int8_t d = e.dir;
                while (b && xs[b - 1] > x) { xs[b] = xs[b - 1]; ds[b] = ds[b - 1]; --b; }
                xs[b] = x; ds[b] = d;
            }
            int wind = 0;
            for (unsigned i = 0; i + 1 < n_active; ++i) {
                wind += ds[i];
                const bool inside = nonzero ? wind != 0 : (i & 1) == 0;
                if (!inside) continue;
                int xa = xs[i], xb = xs[i + 1];
                if (xa < 0) xa = 0;
                if (xb > 480 * Q) xb = 480 * Q;
                if (xb <= xa) continue;
                const int pa = xa >> 4, pb = (xb - 1) >> 4;
                if (pa < lo) lo = pa;
                if (pb > hi) hi = pb;
                if (pa == pb) { cov[pa] += (xb - xa) * 4; continue; }   // a sub-row adds up to 64 a pixel
                cov[pa] += (Q - (xa & 15)) * 4;
                for (int p = pa + 1; p < pb; ++p) cov[p] += 64;
                cov[pb] += (xb - pb * Q) * 4;
            }
        }
        for (unsigned i = 0; i < n_active; ++i) edges[active[i]].x += edges[active[i]].step;
        if ((k & 3) == 3 || k == kmax) {                   // the pixel row is complete
            if (hi >= lo && k >= 0) { cover_row(canvas, k >> 2, lo, hi, colour); for (int p = lo; p <= hi; ++p) cov[p] = 0; }
            lo = 480; hi = -1;
        }
    }
}
void shape_fill(void* canvas, unsigned colour) {
    edges_begin();
    for (unsigned o = 0; o < s_outlines; ++o) {
        if (!s_closed[o] || s_count[o] < 3) continue;
        const int16_t* X = sx + s_first[o]; const int16_t* Y = sy + s_first[o]; const unsigned n = s_count[o];
        for (unsigned i = 0, j = n - 1; i < n; j = i++) edge_add(X[j], Y[j], X[i], Y[i]);
    }
    edges_fill(canvas, false, colour);
}
int isqrt_q(unsigned v) {                                  // bit by bit: 16 steps
    unsigned r = 0, bit = 1u << 30;
    while (bit > v) bit >>= 2;
    while (bit) { if (v >= r + bit) { v -= r + bit; r = (r >> 1) + bit; } else r >>= 1; bit >>= 2; }
    return (int)r;
}
void stroke_quad(int x0, int y0, int x1, int y1, int width) {   // one segment's quad, wound one way, into the table
    const int dx = x1 - x0, dy = y1 - y0;
    int len = isqrt_q((unsigned)(dx * dx + dy * dy));
    if (len == 0) len = 1;
    const int hw = width < 4 ? 2 : width / 2;              // half the width; under a quarter pixel drawn as a quarter
    const int nx = -dy * hw / len, ny = dx * hw / len;
    const int ex = dx * hw / len, ey = dy * hw / len;      // square ends half the width long: joints close
    const int qx[4] = {x0 - ex + nx, x1 + ex + nx, x1 + ex - nx, x0 - ex - nx};
    const int qy[4] = {y0 - ey + ny, y1 + ey + ny, y1 + ey - ny, y0 - ey - ny};
    for (int i = 0, j = 3; i < 4; j = i++) edge_add(qx[j], qy[j], qx[i], qy[i]);
}
void shape_paint(void* canvas, int32_t fill, int32_t stroke, int width, int alpha = 255) {
    if (!s_points || alpha <= 0) return;
    shape_alpha = alpha;
    int top = sy[0], bottom = sy[0], left = sx[0], right = sx[0];
    for (unsigned i = 1; i < s_points; ++i) { if (sy[i] < top) top = sy[i]; if (sy[i] > bottom) bottom = sy[i]; if (sx[i] < left) left = sx[i]; if (sx[i] > right) right = sx[i]; }
    top >>= 4; bottom >>= 4; left >>= 4; right >>= 4;      // pixels
    const bool in_header = @OWN_HEADER@ && bottom <= 40 && top >= -1;
    if (in_header && x2f_mode) {                           // the machine's own header: each column its knob's colour
        static const int32_t field[4] = {0x3C6087, 0x8A653C, 0xCDD0C2, 0xE52817};
        const int column = ((left + right) / 2) / 120;
        const int32_t c = field[column < 0 ? 0 : column > 3 ? 3 : column];
        if (greyish(fill) && fill > 0x101010) fill = tint(fill, c);
        if (greyish(stroke) && stroke > 0x101010) stroke = tint(stroke, c);
    }
    bool any_closed = false;
    for (unsigned o = 0; o < s_outlines; ++o) any_closed = any_closed || s_closed[o];
    if (fill >= 0 && any_closed) shape_fill(canvas, colour565(fill));
    if (in_header && fill >= 0 && any_closed)              // the header cells' lightness, for the text over them
        for (int row = 0; row < 2; ++row)
            if (row ? bottom > 20 : top < 20)
                for (int x = left < 0 ? 0 : left; x <= right && x < 480; x += 4) header_light[row][x >> 2] = (unsigned char)luma(fill);
    if (stroke >= 0 && !(fill >= 0 && stroke == fill && width <= 8)) {   // a hairline the fill's colour adds nothing
        edges_begin();
        for (unsigned o = 0; o < s_outlines; ++o) {
            const int16_t* xs = sx + s_first[o]; const int16_t* ys = sy + s_first[o]; const unsigned n = s_count[o];
            for (unsigned i = 0; i + 1 < n; ++i) stroke_quad(xs[i], ys[i], xs[i + 1], ys[i + 1], width);
            if (s_closed[o] && n > 2) stroke_quad(xs[n - 1], ys[n - 1], xs[0], ys[0], width);
        }
        edges_fill(canvas, true, colour565(stroke));
    }
}

void paint_static(void* canvas, unsigned i) {
    static const opxy::draw::Node identity = {0, 0, 1024, 1024, 0, 0, opxy::draw::keep};
    const FNode& st = F_STATIC[i];
    if (st.shape < 0) {
        const char* t = F_STATIC_TEXT[st.shapes];
        const int px = st.stroke, w = x2f_text::width(t, px);
        x2f_text::draw(canvas, st.x - (st.owner == 1 ? w / 2 : st.owner == 2 ? w : 0), st.y, t, colour565(st.fill), px);
        return;
    }
    shape_begin();
    for (int s = 0; s < st.shapes; ++s) {
        const FShape& sh = F_SHAPES[st.shape + s];
        shape_add(identity, &F_POINTS[sh.first], sh.count, sh.closed != 0);
    }
    shape_paint(canvas, st.fill, st.stroke, st.width, st.alpha);
}

void paint_node(void* canvas, unsigned n);

void paint_frame(void* canvas) {
    for (unsigned k = 0; k < (unsigned)F_NORDER; ++k) {
        if (F_ORDER[k].node) paint_node(canvas, F_ORDER[k].index);
        else if (F_ORDER[k].index < (unsigned)F_NSTATIC) paint_static(canvas, F_ORDER[k].index);
    }
    using namespace opxy::draw;
    for (unsigned t = 0; t < FrameT::text_count; ++t) {
        const Text& text = frame.texts[t];
        if (!text.chars[0]) continue;
        const int px = (t < sizeof F_TEXT_SIZE ? F_TEXT_SIZE[t] : 12) * text.scale >> 10;
        int32_t colour = t < sizeof F_TEXT_COLOUR / sizeof F_TEXT_COLOUR[0] ? F_TEXT_COLOUR[t] : 0xFFFFFF;
        if (@OWN_HEADER@ && x2f_mode && (text.y >> 10) <= 40) {   // a header text over a tinted cell: the machine's
            const int under = luma_under(text.x >> 10, (text.y >> 10) - 4), mine = luma(colour);   // own colour,
            const int apart = mine > under ? mine - under : under - mine;                          // unless it would
            if (apart < 90) colour = under > 128 ? 0x141414 : 0xF2F3F5;                            // be hard to read
        }
        x2f_text::draw(canvas, text.x >> 10, text.y >> 10, text.chars, colour565(colour), px);   // y: the baseline
    }
}

// The SDK's paint is retained: `keep` leaves a node as it was last painted, `stroke` sets only its outline from the
// palette (its fill stays), `fill_and_stroke` both, rgba_* a direct colour. `hidden` hides it in a frame that
// says so and keeps its colours (machines hide every node, then place the visible ones with `keep`).
// Each node starts as the panel drew it.
static int32_t r_fill[FrameT::node_count], r_stroke[FrameT::node_count], r_width[FrameT::node_count];
static int16_t r_alpha[FrameT::node_count];
static unsigned char r_set[FrameT::node_count];
void paint_node(void* canvas, unsigned n) {
    using namespace opxy::draw;
    if (n >= FrameT::node_count) return;
    {
        const Node& node = frame.nodes[n];
        const FNode& own = F_NODES[n];
        if (!r_set[n]) { r_set[n] = 1; r_fill[n] = own.fill; r_stroke[n] = own.stroke; r_width[n] = own.width; r_alpha[n] = (int16_t)own.alpha; }
        if (node.paint == opxy::draw::stroke || node.paint == opxy::draw::fill_and_stroke) {
            const FPal& p = F_PALETTE[node.shade < FrameT::palette_count ? node.shade : 0];
            r_stroke[n] = p.stroke; r_width[n] = p.width; r_alpha[n] = (int16_t)p.alpha;
            if (node.paint == opxy::draw::fill_and_stroke) r_fill[n] = p.fill >= 0 ? p.fill : p.stroke;
        } else if (node.paint == rgba_stroke || node.paint == rgba_fill_and_stroke || node.paint == rgba_fill) {
            const uint32_t w = node.shade;                    // color(): red in the low byte, then green, blue, alpha
            const int32_t rgb = (int32_t)(((w & 255) << 16) | (((w >> 8) & 255) << 8) | ((w >> 16) & 255));
            r_alpha[n] = (int16_t)((w >> 24) & 255);
            if (node.paint != rgba_fill) r_stroke[n] = rgb;
            if (node.paint != rgba_stroke) r_fill[n] = rgb;
        }
        if (node.paint == hidden || node.scale == 0 || node.x >= 480 * 1024 || node.y >= 220 * 1024 ||
            node.x < -64 * 1024 || node.y < -64 * 1024) return;
        int32_t fill = r_fill[n], stroke = r_stroke[n], width = r_width[n], alpha = r_alpha[n];
        const unsigned m_all = own.owner >= 0 ? (unsigned)own.owner & 15u : 0u;
        const bool single = m_all && !(m_all & (m_all - 1u));
        const bool full = x2f_mode == 2u || x2f_mode == 4u, partial = x2f_mode == 1u || x2f_mode == 3u;
        if (own.owner >= 0 && (full || (partial && single))) {   // a grey part an encoder changes
            const int32_t field[4] = {0x3C6087, 0x8A653C, 0xCDD0C2, 0xE52817};
            const unsigned k = ((unsigned)own.owner >> 8) & 3u;     // full: the object's knob (the one moving most of it)
            unsigned only = 0; while (single && !((m_all >> only) & 1u)) ++only;
            const bool object = full && ((unsigned)own.owner & 0x1000u);
            const int32_t c = field[(object ? k : single ? only : k) & 3];
            const bool white_too = full;                         // full: white parts too
            if (greyish(stroke) && (white_too || !whiteish(stroke))) stroke = tint(stroke, c);
            if (greyish(fill) && (white_too || !whiteish(fill))) fill = tint(fill, c);
        }
        if (n < FrameT::path_count) {
            unsigned count = F_NODES[n].shapes ? F_SHAPES[F_NODES[n].shape].count : FrameT::point_count;
            if (count > FrameT::point_count) count = FrameT::point_count;
            shape_begin();
            shape_add(node, (const FPoint*)frame.paths[n].points, count,
                      F_NODES[n].shapes ? F_SHAPES[F_NODES[n].shape].closed != 0 : false);
            shape_paint(canvas, fill, stroke, width, alpha);
        } else {
            shape_begin();
            for (int s = 0; s < own.shapes; ++s) {
                const FShape& sh = F_SHAPES[own.shape + s];
                shape_add(node, &F_POINTS[sh.first], sh.count, sh.closed != 0);
            }
            shape_paint(canvas, fill, stroke, width, alpha);
        }
    }
}

void centred(void* canvas, int middle, int y, const char* text, unsigned colour, int scale) {
    x2f_text::centred(canvas, middle, y + 10 * scale, text, colour, scale > 1 ? 18 : 14);    // y: the top, as it was
}

#define X2F_WHERE where_
#define X2F_OWN_TEXT(i, text) own_header_text(i, text)
bool own_header_text(unsigned i, char* text) {          // the machine's own words for a value, from its display()
    if (i >= 4u || !frame.header[i][0]) return false;
    unsigned c = 0; const char* h = frame.header[i];
    while (h[c] && c < 15) { text[c] = h[c]; ++c; }
    text[c] = 0; return true;
}
inline unsigned x2f_c565(int32_t rgb) { return colour565(rgb); }
@HEADER_C@
}

@HOST_SERVICES@
extern "C" void @P@_shown(unsigned where, unsigned how) OP1F_NAMED("@P@_shown");
extern "C" void @P@_shown(unsigned where, unsigned how) { where_ = where; how_ = how; }

extern "C" int @P@_input(const Op1fInput* e) OP1F_NAMED("@P@_input");
@HELP_C@
extern "C" int @P@_input(const Op1fInput* e) {
    if (e->bytes < 36u) return 0;
    if (x2f_help_input(e)) return 1;
    if (e->what == (unsigned)OP1F_TURN) { if ((unsigned)e->key < 4u) x2f_knobs_held &= ~(1u << e->key); return 0; }
    if (e->what != (unsigned)OP1F_DOWN && e->what != (unsigned)OP1F_DOWN_SHIFT) return 0;   // an up, ours or not
    if (e->key >= 61 && e->key <= 64) { x2f_knobs_held |= 1u << (e->key - 61); return 0; }      // a knob pressed
    if (e->key >= 54 && e->key <= 57) { x2f_knobs_held &= ~(1u << (e->key - 54)); return 0; }   // ... let go
    if (e->key == 38 && e->mine && x2f_knobs_held == 15u) { ++x2f_flips; return 1; }          // shift: the next look
    return 0;
}

extern "C" void @P@_frame(const Op1fFrame* now) OP1F_NAMED("@P@_frame");
extern "C" void @P@_frame(const Op1fFrame* now) {
    if (now->bytes < 40u) return;
    if (!stood) {
        if (op1f_page_stand_in(&@CARD@, 0, 0)) stood = 1;
        else if (!said) { said = 1; op1f_log(2, "x2f/@P@_face.cpp", 1, "@P@: the base did not take its page"); }
    }
    const unsigned how = how_;
    {
        const int kept = op1f_option(&@CARD@, 0);
        if (kept >= 0 && kept <= 4) x2f_mode = (unsigned)kept;
        const unsigned flips = x2f_flips;
        if (flips != x2f_flips_seen) {
            x2f_mode = (x2f_mode + (flips - x2f_flips_seen)) % 5u;
            x2f_flips_seen = flips;
            op1f_option_set(&@CARD@, 0, x2f_mode);
            x2f_said_at = now->ms | 1u;
        }
    }
    if (!(now->page == &@CARD@ && now->canvas != 0 && (how & (unsigned)OP1F_SHOWN_UP))) return;
    if (!scr) scr = (Scratch*)op1f_memory(&@CARD@, 1);   // the screen's memory ask (cleared by the base first)
    if (!scr) return;
    if (!started) {
        for (unsigned n = 0; n < FrameT::node_count; ++n) {
            opxy::draw::Node& node = frame.nodes[n];
            node.x = F_NODES[n].x; node.y = F_NODES[n].y; node.scale = 1024; node.cos = 1024; node.sin = 0;
            node.shade = 0; node.paint = opxy::draw::keep;
        }
        for (unsigned i = 0; i < opxy::page_controls; ++i) input.shown[i] = (opxy::u8)i;
        last_ms = now->ms;
        started = 1;
    }
    unsigned dms = now->ms - last_ms; last_ms = now->ms;
    if (dms > 200) dms = 200;
    input.elapsed = (int32_t)((dms * 1049u) >> 10);         // Q10 seconds
    for (unsigned i = 0; i < (unsigned)@NP@ && i < opxy::parameter_slots; ++i) input.slots[i] = X2F_FIXED[i];
    for (unsigned k = 0; k < (unsigned)X2F_VALUES; ++k) {
        int v = 0;
        if (X2F_MAP[k] < opxy::parameter_slots && op1f_param_heard(&@CARD@, 0, where_, k, &v) == 0)
            input.slots[X2F_MAP[k]] = X2F_ENUM[k][0] < 0 ? amount(v) : X2F_ENUM[k][x2f_zone(v, X2F_ENUM_N[k])];
    }
    // The Field's shift is the OP-XY's encoder press: each position shows its alternate (Field value 4 + k is the
    // alternate of position k). The OP-XY's own shift (its stock synth row) has no counterpart, so it is never held.
    for (unsigned i = 0; i < opxy::page_controls; ++i) {
        const unsigned alt = 4 + i < (unsigned)X2F_VALUES ? X2F_MAP[4 + i] : i;
        const unsigned shown = i < (unsigned)X2F_VALUES ? X2F_MAP[i] : i;   // shift changes what the knobs turn, not
        (void)alt;                                                            // what the header shows
        input.shown[i] = (opxy::u8)shown;
        input.parameters[i] = shown < opxy::parameter_slots ? input.slots[shown] : 0;
    }
    {                                                      // held pitches, as the DSP unit counts them
        unsigned count = 0; uint32_t bits[4] = {0, 0, 0, 0};
        for (unsigned n = 0; n < 128; ++n) if (@P@_held[n]) { ++count; bits[n >> 5] |= 1u << (n & 31u); }
        input.held_notes = count;
        @HELD_PITCHES@
        (void)bits;
    }
    input.note_serial = @P@_note_on;
    input.note_off_serial = @P@_note_off;
    input.shift = 0u;                                      // see the alternates above: the Field's shift is the press
    x2f_shift_held = 0u;
    @NS@::display(state, input, frame);
    paint_frame(now->canvas);
    if (!@OWN_HEADER@) lanes(now->canvas, now->shift ? 1u : 0u);   // its own header: drawn by display(), tinted per column
    if (how & (unsigned)OP1F_SHOWN_OFF) centred(now->canvas, 240, 160, "off", X2F_ENCODER[3], 2);
    if (x2f_help) { x2f_help_paint(now->canvas); return; }
    if (x2f_said_at && now->ms - x2f_said_at < 1200u) {      // a moment's word on which look is on
        static const char* const words[5] = {"colours: own", "colours: partial", "colours: full",
                                             "colours: partial, flipped", "colours: full, flipped"};
        op1f_paint_box(now->canvas, 110, 96, 260, 24, OP1F_COLOUR(0, 0, 0));
        centred(now->canvas, 240, 100, words[x2f_mode], X2F_ENCODER[2], 2);
    }
}
'''

PROBE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <opxy/draw.hpp>
#include "@DISPLAY@"
@PROBE_SERVICES@
static @NS@::Frame frame;
static @NS@::DisplayState state;
static const int fixed[] = { @FIXED@ };
static const int knob_param[] = { @KNOBS@ };          // the machine parameter each Field knob turns
static void run(int control, int value, @NS@::Frame& out) {
    memset(&frame, 0, sizeof frame); memset(&state, 0, sizeof state);
    opxy::draw::Input in; memset(&in, 0, sizeof in);
    in.held_notes = opxy::draw::notes_unavailable;
    for (unsigned i = 0; i < opxy::page_controls; ++i) in.shown[i] = (opxy::u8)i;
    for (unsigned i = 0; i < sizeof fixed / sizeof fixed[0] && i < opxy::parameter_slots; ++i) {
        in.slots[i] = fixed[i]; if (i < opxy::page_controls) in.parameters[i] = fixed[i];
    }
    if (control >= 0) { in.slots[control] = value; if ((unsigned)control < opxy::page_controls) in.parameters[control] = value; }
    // A note starts at frame 5 and is held: what a knob changes about how notes animate shows up too.
    for (int f = 0; f < 40; ++f) {
        in.elapsed = 16;
        if (f == 5) { in.note_serial = 1; in.held_notes = 1; }
        @NS@::display(state, in, frame);
    }
    out = frame;
}
static long long amount_of(const @NS@::Frame& a, const @NS@::Frame& b, unsigned n) {   // how much a node moved
    long long d = llabs((long long)a.nodes[n].x - b.nodes[n].x) + llabs((long long)a.nodes[n].y - b.nodes[n].y) +
                  llabs((long long)a.nodes[n].scale - b.nodes[n].scale) * 64 + llabs((long long)a.nodes[n].cos - b.nodes[n].cos) * 64 +
                  llabs((long long)a.nodes[n].sin - b.nodes[n].sin) * 64 + (a.nodes[n].shade != b.nodes[n].shade ? 50000 : 0);
    if (n < @NS@::Frame::path_count)
        for (unsigned p = 0; p < @NS@::Frame::point_count; ++p)
            d += llabs((long long)a.paths[n].points[p].x - b.paths[n].points[p].x) + llabs((long long)a.paths[n].points[p].y - b.paths[n].points[p].y);
    return d;
}
static bool differs(const @NS@::Frame& a, const @NS@::Frame& b, unsigned n) {
    if (memcmp(&a.nodes[n], &b.nodes[n], sizeof a.nodes[n])) return true;
    return n < @NS@::Frame::path_count && memcmp(&a.paths[n], &b.paths[n], sizeof a.paths[n]);
}
int main() {
    static @NS@::Frame base, lo, hi;
    run(-1, 0, base);
    int owner[@NS@::Frame::node_count];                   // a bit for each knob that moves the node
    static long long most[@NS@::Frame::node_count];       // ... and which moves it most (bits 8..9)
    for (unsigned n = 0; n < @NS@::Frame::node_count; ++n) owner[n] = 0, most[n] = 0;
    // Every Field value: 1-4 on the knobs, 5-8 the same knobs with Shift (the same encoder, the same colour).
    for (int c = 0; c < (int)(sizeof knob_param / sizeof knob_param[0]); ++c) {
        if (knob_param[c] >= (int)opxy::parameter_slots) continue;
        run(knob_param[c], 0, lo); run(knob_param[c], 0x7fffffff, hi);
        for (unsigned n = 0; n < @NS@::Frame::node_count; ++n)
            if (differs(base, lo, n) || differs(base, hi, n) || differs(lo, hi, n)) {
                owner[n] |= 1 << (c & 3);
                const long long m = amount_of(lo, hi, n) + amount_of(base, lo, n) + amount_of(base, hi, n);
                if (m > most[n]) { most[n] = m; owner[n] = (owner[n] & 0xff) | ((c & 3) << 8); }
            }
    }
    printf("%u %u\n", (unsigned)@NS@::Frame::node_count, (unsigned)@NS@::Frame::palette_count);
    for (unsigned n = 0; n < @NS@::Frame::node_count; ++n) printf("%d\n", owner[n]);
}
'''


def probe(display_cpp, ns, fixed, include, workdir, fill, host_compile, exe_suffix, extras=(), knobs=(0, 1, 2, 3)):
    src = os.path.join(workdir, "face_probe.cpp")
    with open(src, "w") as f:
        _, named = host_services(os.path.dirname(display_cpp))
        stubs = {"opxy_synth_shift": '#include <opxy/synth_shift.hpp>\nextern "C" const opxy::draw::ShiftRow* '
                                     'opxy_synth_shift(opxy::u32) { static opxy::draw::ShiftRow row; return &row; }',
                 "sdk_control_display": 'extern "C" opxy::i32 sdk_control_display(opxy::u32) { return 0; }'}
        f.write(fill(PROBE, {"DISPLAY": display_cpp, "NS": ns, "FIXED": fixed, "KNOBS": ", ".join(str(k) for k in knobs) or "0",
                             "PROBE_SERVICES": "\n".join(stubs[k] for k in named)}))
    exe = os.path.join(workdir, "face_probe" + exe_suffix)
    host_compile(["-I" + d for d in include] + [src] + list(extras), exe)
    lines = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.split()
    node_count, palette_count = int(lines[0]), int(lines[1])
    masks = {n: int(v) for n, v in enumerate(lines[2:]) if int(v) & 0xff}
    return node_count, palette_count, masks


COLOUR_MODES = ("partial", "full", "own", "p-flip", "f-flip")
COLOUR_MODES_ORDER = ("own", "partial", "full", "p-flip", "f-flip")   # the pack's option 0, in this order


def owners_for(masks, mode):
    """Which knob's colour each node takes, from the knobs that move it (a bit each), by colour mode:
      partial  a node exactly one knob moves; grey parts only, white kept (playback)
      full     every node a knob moves (the first of them when several do); grey and white parts alike
      own      none: the machine's colours as authored
    Value: the knobs' bits (a part several knobs move takes their colours in turn, moving on at each new note),
    plus 16 when white may be recoloured too. Coloured (non-grey) parts always keep theirs."""
    return dict(masks)          # every look is in the pack: the mode is chosen on the unit (option 0)
    # bits 0..3: the knobs that move a part; bits 8..9: the one that moves it most (full: that one's colour)


# OP-XY host services a screen may call, and what x2f answers with on open-field.
HOST_SERVICES = {
    # The stock synth page's Shift row: on the Field the Shift layer is the machine's values 5-8.
    "opxy_synth_shift": r'''
#include <opxy/synth_shift.hpp>
extern "C" const opxy::draw::ShiftRow* opxy_synth_shift(opxy::u32) {
    static opxy::draw::ShiftRow row;
    row.held = x2f_shift_held; row.line = 0;
    for (unsigned k = 0; k < opxy::draw::shift_entries; ++k) {
        int v = 0;
        row.values[k] = (k < 4 && 4 + k < (unsigned)X2F_VALUES && op1f_param_heard(&@CARD@, 0, where_, 4 + k, &v) == 0)
            ? (X2F_ENUM[4 + k][0] < 0 ? amount(v) : X2F_ENUM[4 + k][x2f_zone(v, X2F_ENUM_N[4 + k])]) : 0;
    }
    return &row;
}''',
    # A track control's displayed value: the machine's own value of that index, as the machine reads it.
    "sdk_control_display": r'''
extern "C" opxy::i32 sdk_control_display(opxy::u32 index) {
    int v = 0;
    if (index >= (unsigned)X2F_VALUES || op1f_param_heard(&@CARD@, 0, where_, index, &v) != 0) return 0;
    return X2F_ENUM[index][0] < 0 ? amount(v) : X2F_ENUM[index][x2f_zone(v, X2F_ENUM_N[index])];
}''',
}


def host_services(machine_path):
    """The service bodies a machine's screen calls (searched in its display sources and headers)."""
    text = ""
    for f in os.listdir(machine_path):
        if f.endswith((".cpp", ".h", ".hpp")) and not f.startswith(("engine", "fx")):
            text += open(os.path.join(machine_path, f), errors="replace").read()
    wanted = [k for k in HOST_SERVICES if k in text or (k == "opxy_synth_shift" and "shift_row(" in text)
              or (k == "sdk_control_display" and "from_control(" in text)]
    return "\n".join(HOST_SERVICES[k] for k in wanted), wanted


# The page header the OP-XY draws, in the knobs' colours and Perun: shared by every page x2f makes (a machine's
# own screen, and the values page of an effect or a machine with no screen), so there is one way it is drawn.
HEADER_C = r'''// The OP-XY's page header, in the knobs' colours: row 1, four cells of a label box (the knob's colour, dark) and the
// value large (the knob's colour); row 2, the same knobs' alternates (shift on the Field), named and valued dim.
int32_t shade(int32_t rgb, int32_t k256) {
    return ((((rgb >> 16) & 255) * k256 >> 8) << 16) | ((((rgb >> 8) & 255) * k256 >> 8) << 8) | ((rgb & 255) * k256 >> 8);
}
bool light(int32_t rgb) { return ((rgb >> 16) & 255) * 3 + ((rgb >> 8) & 255) * 6 + (rgb & 255) > 1500; }
void value_text(unsigned i, char* text) {
    unsigned c = 0;
    int v = 0;
    if (X2F_OWN_TEXT(i, text)) return;                                // the machine's own words for the value
    op1f_param_heard(&@CARD@, @ASK@, X2F_WHERE, i, &v);
    if (X2F_LABELS[i][0][0]) { const char* l = X2F_LABELS[i][x2f_zone(v, X2F_ENUM_N[i])]; while (l[c] && c < 15) { text[c] = l[c]; ++c; } text[c] = 0; return; }
    unsigned p = (unsigned)((v * 100 + 16383) >> 15), d = 0; char dg[4];
    do { unsigned q = 0; while (p >= 10) { p -= 10; ++q; } dg[d++] = (char)('0' + p); p = q; } while (p && d < 3);
    while (d) text[c++] = dg[--d];
    text[c] = 0;
}
void lanes(void* canvas, unsigned shifted) {
    static const int32_t field[4] = {0x3C6087, 0x8A653C, 0xCDD0C2, 0xE52817};
    for (unsigned k = 0; k < 4; ++k) {
        const int x0 = (int)k * 120;
        const int32_t base = field[k], dark = shade(base, 110), dim = shade(base, 150);
        char text[16];
        const unsigned top = shifted && 4 + k < (unsigned)X2F_VALUES ? 4 + k : k;      // shift brings the alternates up
        if (top < (unsigned)X2F_VALUES) {
            op1f_paint_box(canvas, x0, 0, 58, 20, x2f_c565(dark));
            op1f_paint_box(canvas, x0 + 58, 0, 62, 20, x2f_c565(base));
            x2f_text::draw(canvas, x0 + 4, 14, X2F_NAMES[top], x2f_c565(0xF2F3F5), 11);
            value_text(top, text);
            x2f_text::draw(canvas, x0 + 62, 16, text, x2f_c565(light(base) ? 0x101010 : 0xFFFFFF), 18);
        }
        const unsigned low = top == k ? 4 + k : k;
        if (low < (unsigned)X2F_VALUES) {
            x2f_text::draw(canvas, x0 + 4, 33, X2F_NAMES[low], x2f_c565(dim), 11);
            value_text(low, text);
            x2f_text::draw(canvas, x0 + 62, 33, text, x2f_c565(dim), 11);
        }
    }
}
'''


# Help for every page x2f makes: the help key on the pack's page shows this in place of the help of what it stands
# on (dsynth, an effect): what the machine is, what each knob turns, the colour looks, where a sample goes.
HELP_C = r'''
static volatile unsigned x2f_help;
static int x2f_help_input(const Op1fInput* e) {          // the help key, on this pack's page: taken
    if (e->what == (unsigned)OP1F_TURN || e->key != 0 || !e->mine) return 0;   // a turn's key is its knob: 0 is blue
    if (e->what == (unsigned)OP1F_DOWN || e->what == (unsigned)OP1F_DOWN_SHIFT) x2f_help = !x2f_help;
    return 1;
}
static void x2f_help_lines(void* canvas, int x, int* y, const char* text, unsigned colour, int px, int width) {
    char line[200]; unsigned n = 0, word = 0;
    for (const char* c = text; ; ++c) {
        if (*c == ' ' || *c == 0) {
            line[n] = 0;
            if (x2f_text::width(line, px) > width && word) {   // the last word does not fit: the line ends before it
                char rest[200]; unsigned r = 0;
                for (unsigned i = word; i < n && r < 199; ++i) rest[r++] = line[i];
                rest[r] = 0; line[word ? word - 1 : 0] = 0;
                x2f_text::draw(canvas, x, *y, line, colour, px); *y += px + 4;
                n = 0; for (unsigned i = 0; i < r; ++i) line[n++] = rest[i];
            }
            if (*c == 0) break;
            if (n < sizeof line - 1) { word = n + 1; line[n++] = ' '; }
        } else if (n < sizeof line - 1) line[n++] = *c;
    }
    line[n] = 0;
    if (n) { x2f_text::draw(canvas, x, *y, line, colour, px); *y += px + 4; }
}
static void x2f_help_paint(void* canvas) {
    static const unsigned knob[4] = {OP1F_COLOUR(0x3c, 0x60, 0x87), OP1F_COLOUR(0x8a, 0x65, 0x3c),
                                     OP1F_COLOUR(0xcd, 0xd0, 0xc2), OP1F_COLOUR(0xe5, 0x28, 0x17)};
    const unsigned ink = OP1F_COLOUR(0xf2, 0xf3, 0xf5), dim = OP1F_COLOUR(0xa2, 0xa4, 0xa6);
    op1f_paint_box(canvas, 0, 0, 480, 220, OP1F_COLOUR(0, 0, 0));
    int y = 22;
    x2f_text::draw(canvas, 12, y, "@NAME@", ink, 18);
    x2f_text::draw(canvas, 12 + x2f_text::width("@NAME@", 18) + 10, y, "an Open-XY machine, ported by x2f", dim, 11);
    y += 20;
    x2f_help_lines(canvas, 12, &y, "@DESC@", ink, 14, 456);
    y += 4;
    for (unsigned k = 0; k < 4; ++k) {                    // what each knob turns, and with shift
        char line[48]; unsigned n = 0;
        const char* a = k < (unsigned)X2F_VALUES ? X2F_NAMES[k] : "";
        const char* b = 4 + k < (unsigned)X2F_VALUES ? X2F_NAMES[4 + k] : "";
        while (*a && n < 20) line[n++] = *a++;
        if (*b) { const char* w = "  /  shift: "; while (*w) line[n++] = *w++; while (*b && n < 46) line[n++] = *b++; }
        line[n] = 0;
        op1f_paint_box(canvas, 12, y - 9, 10, 10, knob[k]);
        x2f_text::draw(canvas, 28, y, line, ink, 11);
        y += 15;
    }
    y += 4;
    @HELP_EXTRA@
    x2f_text::draw(canvas, 12, 214, "help: back", dim, 11);
}
'''
