// Text for the packs' pages in Perun (runtime/x2f_font.h, SIL OFL 1.1): each letter's 4-bit coverage widened to a
// byte and painted with op1f_paint_mask in the colour asked, so letters have soft edges at any colour. Placed by
// their baseline, as an SVG text is. Sizes: the nearest of the font's (11, 14, 18 pixels).
#pragma once
#include "x2f_font.h"

namespace x2f_text {
inline const x2f_font::Face& face(int px) {
    const x2f_font::Face* best = &x2f_font::faces[0];
    for (const x2f_font::Face& f : x2f_font::faces)
        if ((f.size > px ? f.size - px : px - f.size) < (best->size > px ? best->size - px : px - best->size)) best = &f;
    return *best;
}
inline int width(const char* s, int px) {
    const x2f_font::Face& f = face(px);
    int w = 0;
    for (; *s; ++s) if (*s >= 32 && *s < 127) w += f.glyphs[*s - 32].advance;
    return w;
}
inline void draw(void* canvas, int x, int baseline, const char* s, unsigned colour, int px) {
    const x2f_font::Face& f = face(px);
    static unsigned char cover[40 * 40];
    for (; *s; ++s) {
        if (*s < 32 || *s >= 127) continue;
        const x2f_font::Glyph& g = f.glyphs[*s - 32];
        if (g.w && g.h && (unsigned)g.w * g.h <= sizeof cover) {
            for (unsigned i = 0; i < (unsigned)g.w * g.h; ++i) {
                const unsigned b = f.bits[g.at + (i >> 1)];
                cover[i] = (unsigned char)(((i & 1) ? b >> 4 : b & 15) * 17);
            }
            op1f_paint_mask(canvas, x + g.left, baseline + g.top, g.w, g.h, cover, g.w, colour);
        }
        x += g.advance;
    }
}
inline void centred(void* canvas, int middle, int baseline, const char* s, unsigned colour, int px) {
    draw(canvas, middle - width(s, px) / 2, baseline, s, colour, px);
}
}
