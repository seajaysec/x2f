// x2f runtime: what an Open-XY machine needs on open-field that the pack links nothing for.
//
// 1. Division and memory helpers. A pack links no runtime library, and Open-XY machines divide (the OP-XY's
//    toolchain links libgcc). gcc emits calls to these names for '/' and '%', and for struct clears/copies.
//    They are defined once a pack, in its DSP unit, under the exact names gcc calls, global so that the pack's
//    other units (its screen, a machine's other sources: X2F_NO_LIBCALLS) reach them too. A pack's globals are
//    not offered to other packs (its recipe exports nothing), so packs never clash on them.
//    Shift-subtract: no hardware divide on Blackfin.
//    Cost: ~32 iterations a call; a machine that divides per sample pays it on the audio thread.
// 2. A 2x upsampler, 44.1 kHz machine blocks -> 88.2 kHz engine blocks: the Kaiser halfband-127 from
//    Tekkon-1's open_xy backend (resampler_coefficients.hpp, Q30), with input headroom of 2 bits instead of
//    Tekkon's peak gate, and the odd phase taken straight from the delayed input (a halfband's centre tap).
#pragma once
#include <stdint.h>
#include <opxy/q31.hpp>

#if !defined(OP1F_HOST) && !defined(X2F_NO_LIBCALLS)   // once a pack: the face unit uses the DSP unit's
#define X2F_LIBCALL(name) asm(name) __attribute__((used))
extern "C" {
unsigned x2f_udiv(unsigned n, unsigned d) X2F_LIBCALL("___udivsi3");
unsigned x2f_umod(unsigned n, unsigned d) X2F_LIBCALL("___umodsi3");
int x2f_div(int n, int d) X2F_LIBCALL("___divsi3");
int x2f_mod(int n, int d) X2F_LIBCALL("___modsi3");
void* x2f_memset(void* p, int v, unsigned long n) X2F_LIBCALL("_memset");
void* x2f_memcpy(void* d, const void* s, unsigned long n) X2F_LIBCALL("_memcpy");
void* x2f_memmove(void* d, const void* s, unsigned long n) X2F_LIBCALL("_memmove");
}
// 64-bit helpers: machines that use int64 (the mda ports) call these. Written in 32-bit pieces so that
// nothing here calls itself. Slow (a 64-step loop to divide): the Field's audio rules say avoid 64-bit work.
extern "C" {
unsigned long long x2f_muldi3(unsigned long long a, unsigned long long b) X2F_LIBCALL("___muldi3");
unsigned long long x2f_udivdi3(unsigned long long n, unsigned long long d) X2F_LIBCALL("___udivdi3");
unsigned long long x2f_umoddi3(unsigned long long n, unsigned long long d) X2F_LIBCALL("___umoddi3");
long long x2f_divdi3(long long n, long long d) X2F_LIBCALL("___divdi3");
long long x2f_moddi3(long long n, long long d) X2F_LIBCALL("___moddi3");
}
static unsigned long long x2f_mul32(unsigned a, unsigned b) {   // 32 x 32 -> 64 from 16 x 16 products
    const unsigned al = a & 0xffffu, ah = a >> 16, bl = b & 0xffffu, bh = b >> 16;
    const unsigned ll = al * bl, lh = al * bh, hl = ah * bl, hh = ah * bh;
    const unsigned mid = (ll >> 16) + (lh & 0xffffu) + (hl & 0xffffu);
    const unsigned lo = (ll & 0xffffu) | (mid << 16);
    const unsigned hi = hh + (lh >> 16) + (hl >> 16) + (mid >> 16);
    return ((unsigned long long)hi << 32) | lo;
}
unsigned long long x2f_muldi3(unsigned long long a, unsigned long long b) {
    const unsigned al = (unsigned)a, ah = (unsigned)(a >> 32), bl = (unsigned)b, bh = (unsigned)(b >> 32);
    return x2f_mul32(al, bl) + ((unsigned long long)(ah * bl + al * bh) << 32);
}
static unsigned long long x2f_udivmod64(unsigned long long n, unsigned long long d, unsigned long long* rem) {
    if (d == 0) { *rem = n; return ~0ull; }
    unsigned long long q = 0, r = 0;
    for (int bit = 63; bit >= 0; --bit) {
        r = (r << 1) | ((n >> bit) & 1u);
        if (r >= d) { r -= d; q |= 1ull << bit; }
    }
    *rem = r;
    return q;
}
unsigned long long x2f_udivdi3(unsigned long long n, unsigned long long d) { unsigned long long r; return x2f_udivmod64(n, d, &r); }
unsigned long long x2f_umoddi3(unsigned long long n, unsigned long long d) { unsigned long long r; x2f_udivmod64(n, d, &r); return r; }
long long x2f_divdi3(long long n, long long d) {
    unsigned long long un = n < 0 ? 0ull - (unsigned long long)n : (unsigned long long)n;
    unsigned long long ud = d < 0 ? 0ull - (unsigned long long)d : (unsigned long long)d, r;
    unsigned long long q = x2f_udivmod64(un, ud, &r);
    return (n < 0) != (d < 0) ? (long long)(0ull - q) : (long long)q;
}
long long x2f_moddi3(long long n, long long d) {
    unsigned long long un = n < 0 ? 0ull - (unsigned long long)n : (unsigned long long)n;
    unsigned long long ud = d < 0 ? 0ull - (unsigned long long)d : (unsigned long long)d, r;
    x2f_udivmod64(un, ud, &r);
    return n < 0 ? (long long)(0ull - r) : (long long)r;
}
static unsigned x2f_udivmod(unsigned n, unsigned d, unsigned* rem) {
    if (d == 0) { *rem = n; return 0xffffffffu; }
    unsigned q = 0, r = 0;
    for (int bit = 31; bit >= 0; --bit) {
        r = (r << 1) | ((n >> bit) & 1u);
        if (r >= d) { r -= d; q |= 1u << bit; }
    }
    *rem = r;
    return q;
}
unsigned x2f_udiv(unsigned n, unsigned d) { unsigned r; return x2f_udivmod(n, d, &r); }
unsigned x2f_umod(unsigned n, unsigned d) { unsigned r; x2f_udivmod(n, d, &r); return r; }
int x2f_div(int n, int d) {
    unsigned un = n < 0 ? 0u - (unsigned)n : (unsigned)n, ud = d < 0 ? 0u - (unsigned)d : (unsigned)d, r;
    unsigned q = x2f_udivmod(un, ud, &r);
    return (n < 0) != (d < 0) ? (int)(0u - q) : (int)q;
}
int x2f_mod(int n, int d) {
    unsigned un = n < 0 ? 0u - (unsigned)n : (unsigned)n, ud = d < 0 ? 0u - (unsigned)d : (unsigned)d, r;
    x2f_udivmod(un, ud, &r);
    return n < 0 ? (int)(0u - r) : (int)r;
}
void* x2f_memset(void* p, int v, unsigned long n) {
    unsigned char* b = (unsigned char*)p;
    while (n--) *b++ = (unsigned char)v;
    return p;
}
void* x2f_memcpy(void* d, const void* s, unsigned long n) {
    unsigned char* o = (unsigned char*)d; const unsigned char* i = (const unsigned char*)s;
    while (n--) *o++ = *i++;
    return d;
}
void* x2f_memmove(void* d, const void* s, unsigned long n) {
    unsigned char* o = (unsigned char*)d; const unsigned char* i = (const unsigned char*)s;
    if (o < i) { while (n--) *o++ = *i++; }
    else { o += n; i += n; while (n--) *--o = *--i; }
    return d;
}
#endif

namespace x2f {
static const int32_t kHalfband[64] = {
    -14458,37855,-76308,135308,-221492,342741,-508268,728704,
    -1016176,1384402,-1848788,2426571,-3137020,4001728,-5045055,6294796,
    -7783187,9548425,-11636972,14107092,-17034364,20520480,-24707740,29803902,
    -36127016,44191730,-54889766,69911004,-92886248,133272301,-225776530,682873261,
    682873261,-225776530,133272301,-92886248,69911004,-54889766,44191730,-36127016,
    29803902,-24707740,20520480,-17034364,14107092,-11636972,9548425,-7783187,
    6294796,-5045055,4001728,-3137020,2426571,-1848788,1384402,-1016176,
    728704,-508268,342741,-221492,135308,-76308,37855,-14458,
};

inline int32_t saturate_shift2(int32_t v) {
    if (v > 0x1fffffff) return 0x7fffffff;
    if (v < -0x20000000) return (int32_t)0x80000000u;
    return (int32_t)((uint32_t)v << 2);
}

// A Kaiser (beta 7) 63-tap halfband, 16 coefficient pairs, Q30, outermost first: half the cost of the 127-tap one
// above (32 pairs), which mattered on the unit: a stereo machine pays it twice, inside the engine's per-voice
// budget. The odd phase is the delayed input (a halfband's centre tap).
static const int32_t kHalfband16[16] = { -130794, 450782, -1053016, 2067686, -3655319, 6010695, -9370926, 14032849,
    -20390020, 29011547, -40816260, 57489375, -82623299, 125603575, -221037457, 681281495 };

// One channel's 2x interpolator; 15 input samples (0.35 ms) of latency.
struct Upsampler {
    enum { P = 16 };
    int32_t history[4 * P];
    uint32_t cursor;
    void reset() { for (unsigned i = 0; i < 4u * P; ++i) history[i] = 0; cursor = 0; }
    void sample(int32_t input, int32_t& even, int32_t& odd) {
        const int32_t x = input >> 2;   // headroom: pairs stay inside Q31, the sum inside Q31
        history[cursor] = history[cursor + 2 * P] = x;
        const int32_t* hi = &history[cursor + 2 * P];
        const int32_t* lo = &history[cursor + 1];
        int32_t sum = 0;
        for (unsigned k = 0; k < P; ++k) sum += opxy::q31::multiply(hi[-(int)k] + lo[k], kHalfband16[k]);
        even = saturate_shift2(sum * 2);
        odd = saturate_shift2(history[(cursor + P + 1) & (2 * P - 1)]);
        cursor = (cursor + 1) & (2 * P - 1);
    }
};
}
