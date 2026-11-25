// The open-field card header on the unit; on the host (OP1F_HOST, 64-bit pointers, where the header's 32-bit
// layout asserts fail) a mirror of just the names a translated pack uses, for the host check.
#pragma once
#ifndef OP1F_HOST
#include "build_r6e_card.h"
#else
#define OP1F_NAMED(x)
#define OP1F_COLOUR(r, g, b) ((((unsigned)(r) & 0xf8u) << 8) | (((unsigned)(g) & 0xfcu) << 3) | (((unsigned)(b) & 0xffu) >> 3))
struct Op1fCard { unsigned mark; };
struct Op1fFrame { unsigned bytes, frame, ms, mode, screen, shift, popup; const Op1fCard* page; unsigned disk; void* canvas; };
struct Op1fVoiceBlock { unsigned bytes, slot, voice, frames, rate; int* out; const int* values;
                        unsigned step; int velocity; unsigned events, period, playing; void* state; };
struct Op1fEffectBlock { unsigned bytes, place, frames; int* left; int* right; const int* values;
                         unsigned on, fresh, period, playing; void* state; };
enum { OP1F_VOICE_BEGINS = 1, OP1F_VOICE_ENDS = 2, OP1F_VOICE_FRESH = 4 };
enum { OP1F_SHOWN_UP = 1, OP1F_SHOWN_OFF = 2, OP1F_SHOWN_COVERED = 4 };
struct Op1fInput { unsigned bytes, what, key; int turn; unsigned mode, screen, shift, mine; const unsigned* record; unsigned thread; };
enum { OP1F_DOWN = 1, OP1F_DOWN_SHIFT = 2, OP1F_UP = 3, OP1F_TURN = 4 };
extern "C" int op1f_option(const Op1fCard* card, unsigned option);
extern "C" void op1f_paint_mask(void* canvas, int x, int y, int w, int h, const unsigned char* cover, int stride, unsigned colour);
extern "C" int op1f_list(const Op1fCard* card, unsigned folder, char* names, unsigned room);
extern "C" int op1f_read(const Op1fCard* card, unsigned folder, const char* name, void* buffer, unsigned room);
extern "C" void* op1f_memory(const Op1fCard* card, unsigned ask);
enum { OP1F_NO_FILE = -1, OP1F_TOO_LARGE = -2, OP1F_NOT_NOW = -3, OP1F_NO_FOLDER = -4 };
extern "C" int op1f_option_set(const Op1fCard* card, unsigned option, unsigned value);
extern "C" void op1f_log(int level, const char* file, int line, const char* format, ...);
extern "C" void op1f_paint_box(void* canvas, int x, int y, int w, int h, unsigned colour);
extern "C" void op1f_paint_line(void* canvas, int x0, int y0, int x1, int y1, int width, unsigned colour);
extern "C" int op1f_paint_text(void* canvas, int x, int y, const char* text, unsigned colour, int scale);
extern "C" int op1f_page_stand_in(const Op1fCard* card, unsigned ask, void* document);
extern "C" int op1f_param_heard(const Op1fCard* card, unsigned ask, unsigned place, unsigned index, int* out);
#endif
