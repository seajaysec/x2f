#!/usr/bin/env python3
"""x2f: translate an Open-XY machine (synth or effect) into an open-field pack.

    python x2f.py setup                          point x2f at what it needs (asks; can download), remembered
    python x2f.py where | reset [KEY...] | clone open_field|open_xy|machines [--from URL]
                                                 what is remembered / forget it / git clone a missing one
    python x2f.py translate <machine dir> [--out out/<id>] [--name NAME] [--cycles N] [--rides EFFECT]
    python x2f.py build <machine dir> ...        translate, cross-compile, write the card, link <name>.pack
    python x2f.py check                          the one check: karplus (synth) and ringmod (fx) through
                                                  translation, a host render, and a full pack build

See README.md for what is mapped and how.
"""
import argparse, json, math, os, re, shutil, struct, subprocess, sys, tempfile, time, wave, zlib
import face

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "x2f.config.json")
EXE = ".exe" if os.name == "nt" else ""
# The outside things x2f needs. There are no defaults: the first run asks where each one is (or offers to fetch
# it), remembers the answer in x2f.config.json, and asks again if a remembered place stops holding it.
REPOS = {"open_field": "https://github.com/LaP0573/open-field.git",
         "open_xy": "https://github.com/etcetc000/open-xy.git",
         "machines": "https://github.com/etcetc000/open-xy-core-machines.git"}
SETTINGS = {   # key: (flag, what it is, how to tell it is the right place)
    "open_field": ("--open-field", "your open-field checkout", "tools/pack_build.py"),
    "open_xy": ("--open-xy", "your open-xy checkout", "sdk/include/opxy"),
    "machines": ("--machines", "a folder of Open-XY machines (open-xy-core-machines/machines), for `check`", "karplus/machine.json"),
    "host_cxx": ("--host-cxx", "a C++17 compiler for this computer (clang++ or g++)", None),
}
NEEDS = {"translate": ("open_field", "open_xy", "host_cxx"), "build": ("open_field", "open_xy", "host_cxx"),
         "check": ("open_field", "open_xy", "host_cxx", "machines")}
CFG = {}


def load_config():
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(saved):
    with open(CONFIG_FILE, "w") as f:
        json.dump(saved, f, indent=1)


def absolute(value):
    return os.path.abspath(os.path.join(HERE, os.path.expanduser(value)))


def valid(key, value):
    """None when `value` is right for `key`, else why not."""
    if not value:
        return "not set"
    if key == "host_cxx":
        exe = shutil.which(value) or (value if os.path.isfile(value) else None)
        if not exe:
            return "%s is not a program on this computer" % value
        with tempfile.TemporaryDirectory() as tmp:
            src, out = os.path.join(tmp, "t.cpp"), os.path.join(tmp, "t" + EXE)
            with open(src, "w") as f:
                f.write("#include <stdio.h>\nint main() { constexpr auto f = [](int x) { return x; }; printf(\"%d\", f(1)); }\n")
            if subprocess.run([exe, "-std=c++17", src, "-o", out], capture_output=True).returncode:
                return "%s did not compile C++17" % value
        return None
    path = absolute(value)
    if not os.path.isdir(path):
        return "%s is not a folder (moved or deleted?)" % path
    if not os.path.exists(os.path.join(path, SETTINGS[key][2])):
        return "%s has no %s: not %s" % (path, SETTINGS[key][2], SETTINGS[key][1])
    if key == "open_xy" and not os.path.isfile(os.path.join(path, "local", "toolchain", "bin", "bfin-elf-g++" + EXE)):
        return "%s has no Blackfin toolchain yet (python %s toolchain fetch)" % (path, os.path.join(path, "sdk.py"))
    return None


def repo_url(key):
    """The repository to clone for `key`: the one last used (x2f.config.json "repos"), else the project's home as
    of this writing (REPOS). Projects change hands; `clone --from URL` or the prompt points elsewhere."""
    return load_config().get("repos", {}).get(key) or REPOS[key]


def clone(key, parent=None, url=None):
    """git clone `key`'s repository into a folder the person chooses; for open-xy, fetch its toolchain too.
    Returns the place to remember, or None."""
    if not shutil.which("git"):
        print("  git is not installed: get it from git-scm.com (or your package manager), or clone it yourself.")
        return None
    if not url:
        url = repo_url(key)
        if not parent:
            url = prompt("  Repository to clone [%s]: " % url).strip() or url
    parent = parent or prompt("  Clone it into which folder? ").strip()
    if not parent:
        return None
    parent = absolute(parent)
    os.makedirs(parent, exist_ok=True)
    name = url.rstrip("/").split("/")[-1].split(":")[-1]
    name = name[:-4] if name.endswith(".git") else name
    target = os.path.join(parent, name)
    if not os.path.isdir(target):
        subprocess.run(["git", "clone", url, target], check=True)
    else:
        print("  %s is already there: using it as it is." % target)
    if key == "open_xy" and not os.path.isfile(os.path.join(target, "local", "toolchain", "bin", "bfin-elf-g++" + EXE)):
        subprocess.run([sys.executable, os.path.join(target, "sdk.py"), "toolchain", "fetch"], cwd=target, check=True)
    saved = load_config()
    saved.setdefault("repos", {})[key] = url
    save_config(saved)
    return os.path.join(target, "machines") if key == "machines" and os.path.isdir(os.path.join(target, "machines")) else target


def prompt(text):
    try:
        return input(text)
    except EOFError:
        return ""


def ask(key, why):
    flag, what, _ = SETTINGS[key]
    print("\nx2f needs %s (%s)." % (what, why))
    found = [c for c in ("clang++", "g++", "c++") if shutil.which(c)] if key == "host_cxx" else []
    while True:
        if key == "host_cxx":
            hint = " Found on PATH: %s." % ", ".join(found) if found else \
                {"nt": " None found: install LLVM (clang++) or MSYS2's mingw-w64 g++."}.get(os.name,
                 " None found: macOS `xcode-select --install`; Debian/Ubuntu `sudo apt install g++`; Fedora `sudo dnf install gcc-c++`.")
            answer = prompt("  Which compiler?%s Type its name or path (blank to stop): " % hint).strip()
        else:
            answer = prompt("  Type its folder, or `clone` to git clone it now (blank to stop): ").strip()
            if answer.lower() == "clone":
                answer = clone(key) or ""
        if not answer:
            raise SystemExit("x2f: stopped. Point to it any time: python x2f.py setup %s <%s>"
                             % (flag, "compiler" if key == "host_cxx" else "folder"))
        problem = valid(key, answer)
        if problem is None:
            return answer if key == "host_cxx" else absolute(answer)
        print("  That won't do: %s." % problem)


def configure(command, flags=None):
    """Fill CFG with what `command` needs: a flag given now, else what was remembered. Anything missing or no longer
    valid is asked for (interactive) or reported with the command that fixes it (not interactive)."""
    flags = {k: v for k, v in (flags or {}).items() if v}
    saved = load_config()
    changed = False
    for key in NEEDS.get(command, ()):
        value = flags.get(key) or saved.get(key)
        problem = valid(key, value)
        if problem and value and not flags.get(key):
            print("x2f: what was remembered for %s (%s) no longer works: %s." % (key, SETTINGS[key][1], problem))
        if problem:
            if not sys.stdin.isatty():
                raise SystemExit("x2f: %s: %s. Run: python x2f.py setup %s <%s>"
                                 % (SETTINGS[key][1], problem, SETTINGS[key][0], "compiler" if key == "host_cxx" else "folder"))
            value = ask(key, "remembered after this")
        elif key != "host_cxx":
            value = absolute(value)
        CFG[key] = value
        if saved.get(key) != value:
            saved[key] = value
            changed = True
    if changed:
        save_config(saved)
        print("x2f: remembered in %s (python x2f.py where; python x2f.py reset to forget)" % CONFIG_FILE)


def opxy_include():
    return os.path.join(CFG["open_xy"], "sdk", "include")


def freestanding_include():
    """open-xy's own C++ library headers for the Field build (<algorithm>, <cstdio>, <cstring> ...)."""
    return os.path.join(CFG["open_xy"], "sdk", "platform", "blackfin", "freestanding")


def toolchain_dir():
    return os.path.join(CFG["open_xy"], "local", "toolchain", "bin")


def profile():
    return os.path.join(CFG["open_field"], "profiles", "op1f-1.7.3.json")


def cpath(path):
    """A path as C source spells it: forward slashes on every OS."""
    return path.replace("\\", "/")


Q31_MAX = 0x7fffffff
STOCK_EFFECTS = ("delay", "grid", "punch", "phone", "spring", "nitro", "cwo", "terminal")
# open-xy's firmware flags (open-field's tools/module_build.py), but C++17 and no -Werror: the machines are
# written against the OP-XY SDK's C++ level, and their warnings are theirs.
CXX = ["-mcpu=bf707", "-O2", "-std=gnu++17", "-ffreestanding", "-fno-exceptions", "-fno-rtti",
       "-fno-threadsafe-statics", "-fno-use-cxa-atexit", "-fno-asynchronous-unwind-tables", "-fno-unwind-tables",
       "-fno-common", "-fno-tree-loop-distribute-patterns", "-fomit-frame-pointer", "-nostdinc++", "-w",
       "-fno-schedule-insns2"]   # bundling emits dsp32shiftimm + P-reg store pairs the assembler refuses (anomaly 05000074)


# ------------------------------------------------------------------------------------------- reading a machine

def field_name(text, *fallbacks):
    """open-field names: 1-15 of a-z 0-9 ' ' '-' '_'; the first of text and fallbacks that leaves one."""
    for t in (text,) + fallbacks:
        n = re.sub(r"[^a-z0-9 _-]", "", (t or "").lower().replace(".", " "))[:15].strip()
        if n:
            return n
    raise SystemExit("no usable open-field name in %r" % (text,))


def symbol(text):
    return re.sub(r"[^a-z0-9]", "_", text.lower())


def read_machine(path):
    path = os.path.abspath(path)
    with open(os.path.join(path, "machine.json")) as f:
        machine = json.load(f)
    with open(os.path.join(path, "definition.json")) as f:
        definition = json.load(f)
    kind = machine.get("kind")
    if kind not in ("synth", "effect", "drum", "sample"):
        raise SystemExit("%s: kind %r is not translated (synths, drum kits, samplers and effects are)" % (path, kind))
    source = os.path.join(path, {"synth": "engine.cpp", "drum": "engine.cpp", "sample": "sample.cpp"}.get(kind, "fx.cpp"))
    if not os.path.isfile(source):
        raise SystemExit("%s: no %s" % (path, os.path.basename(source)))
    core_path = os.path.join(path, "core.h")
    with open(core_path if os.path.isfile(core_path) else source, errors="replace") as f:    # samplers may have none
        core = f.read()
    ns = re.search(r"namespace\s+(\w+)\s*\{", core)
    if not ns:
        raise SystemExit("%s: neither core.h nor %s opens a namespace" % (path, os.path.basename(source)))
    # The machine's other translation units: machine.json "sources" when it lists them (as the SDK builds it),
    # else every .cpp beside the entry point but the screen and the entry points themselves.
    if machine.get("sources"):
        extras = [os.path.join(path, x) for x in machine["sources"]]
    else:
        extras = sorted(os.path.join(path, f) for f in os.listdir(path)
                        if f.endswith(".cpp") and f not in ("engine.cpp", "fx.cpp", "display.cpp", "sample.cpp"))
    missing = [x for x in extras if not os.path.isfile(x)]
    if missing:
        raise SystemExit("%s: machine.json lists sources that are not there: %s" % (path, ", ".join(missing)))
    includes = [os.path.join(path, x) for x in machine.get("includes", [])]
    extra = load_config().get("includes", {}).get(path, [])        # x2f.py build ... --include DIR, remembered
    with open(source) as f:
        entry = f.read()
    # Private tables written for every engine of a family under `namespace md` (the MNMD captures): each engine
    # keeps its own copy in its own namespace (its headers say `namespace md = md_<engine>;`, as that repository's
    # tools/make_engine.py writes them). x2f makes that scoped copy for this machine; the captured files stay as
    # they are.
    scope = None
    for f in os.listdir(path):
        if f.endswith((".h", ".hpp")):
            hit = re.search(r"namespace md = (md_\w+);", open(os.path.join(path, f), errors="replace").read())
            if hit:
                scope = hit.group(1)
                break
    for d in extra:
        heads = [f for f in os.listdir(d) if f.endswith((".h", ".hpp"))] if os.path.isdir(d) else []
        if scope and any("namespace md {" in open(os.path.join(d, f), errors="replace").read() for f in heads):
            scoped = os.path.join(HERE, "out", ".scoped", scope)
            os.makedirs(scoped, exist_ok=True)
            for f in heads:
                t = open(os.path.join(d, f), errors="replace").read().replace(
                    "namespace md {", "namespace %s {} namespace md = %s; namespace %s {" % (scope, scope, scope))
                open(os.path.join(scoped, f), "w").write(t)
            includes.append(scoped)
        else:
            includes.append(d)
    drum = kind == "drum"
    sampler = kind == "sample"
    kind = "synth" if drum or sampler else kind                 # a kit is a synth engine on the Field: one key, one drum
    return {"sampler": sampler, "drum": drum, "path": path, "machine": machine, "definition": definition, "kind": kind, "source": source,
            "core": core + "\n" + entry, "ns": ns.group(1), "extras": extras, "includes": includes}


def zone_centre(index, n):
    """The middle of choice `index`'s zone of a knob's travel (0..32767 in n zones)."""
    return min(32767, ((2 * index + 1) * 32768) // (2 * n))


def params_of(m, most):
    """Each machine parameter as an open-field value. AMOUNT: 0..32767, handed to the machine as Tekkon-1 hands
    it (v * 65538, 32767 -> 2^31 - 1). ENUM of n labels: 0..n-1, handed as the knob word of its k-th detent,
    floor((k (2^31 - 1) + (n - 1) / 2) / (n - 1)), which the machine's own pick reads back as k."""
    out = []
    for p in m["definition"]["parameters"]:
        mapping = p.get("mapping", {})
        labels = mapping.get("labels") if mapping.get("kind") == "enum" else None
        default = float(p.get("default", 0))
        if labels:
            n = len(labels)
            start = default if default >= 1 and float(default).is_integer() and default < n else round(default * (n - 1))
            table = [((k * Q31_MAX + (n - 1) // 2) // (n - 1)) if n > 1 else 0 for k in range(n)]
            index = int(min(max(start, 0), n - 1))
            # the knob's whole travel, in n equal zones: the Field rounds a turn onto a value's range, so 0..n-1
            # would leave the last option at the very end of the travel and others hard to reach
            out.append({"control": p.get("control"), "id": p["id"], "name": field_name(p.get("label"), p["id"], "value %d" % len(out)), "low": 0, "high": 32767,
                        "start": zone_centre(index, n), "index": index, "enum": table, "labels": labels})
        else:
            out.append({"control": p.get("control"), "id": p["id"], "name": field_name(p.get("label"), p["id"], "value %d" % len(out)), "low": 0, "high": 32767,
                        "start": int(round(min(max(default, 0.0), 1.0) * 32767)), "enum": None})
    return out


def guess_rides(m):
    text = (m["definition"].get("id", "") + " " + m["machine"].get("description", "")).lower()
    for words, ride in ((("delay", "echo", "pong", "tape"), "delay"), (("reverb", "room", "hall", "spring"), "spring"),
                        (("drive", "dist", "crush", "fuzz", "satur"), "punch"), (("filter", "phone", "eq"), "phone")):
        if any(w in text for w in words):
            return ride
    return "punch"


# ------------------------------------------------------------------------------------------------ generation

PAGE = r'''
// ---- the page: the base's blank page, the values painted as the sound plays them (op1f_param_heard).
#include "x2f_text.h"
static const unsigned X2F_KNOB[4] = {OP1F_COLOUR(0x3c, 0x60, 0x87), OP1F_COLOUR(0x8a, 0x65, 0x3c),
                                     OP1F_COLOUR(0xcd, 0xd0, 0xc2), OP1F_COLOUR(0xe5, 0x28, 0x17)};   // op1f-design encoder colours
static const unsigned X2F_GREY = OP1F_COLOUR(0xa2, 0xa4, 0xa6);
static volatile unsigned x2f_where, x2f_how;
static unsigned x2f_stood, x2f_said;

extern "C" void @P@_shown(unsigned where, unsigned how) OP1F_NAMED("@P@_shown");
extern "C" void @P@_shown(unsigned where, unsigned how) { x2f_where = where; x2f_how = how; }

static void x2f_number(char* out, int n) {
    unsigned at = 0, u = n < 0 ? (unsigned)-n : (unsigned)n, d = 0;
    char digits[12];
    if (n < 0) out[at++] = '-';
    do { unsigned tens = 0; while (u >= 10u) { u -= 10u; ++tens; } digits[d++] = (char)('0' + u); u = tens; } while (u && d < 11u);
    while (d) out[at++] = digits[--d];
    out[at] = 0;
}

static void x2f_centred(void* canvas, int middle, int y, const char* text, unsigned colour, int scale) {
    x2f_text::centred(canvas, middle, y + (scale > 2 ? 21 : scale > 1 ? 14 : 10), text, colour, scale > 2 ? 18 : scale > 1 ? 14 : 11);
}

// The header the OP-XY draws for a machine, the same code a machine's own screen uses (face.HEADER_C).
static unsigned x2f_where_now;
#define X2F_WHERE x2f_where_now
#define X2F_OWN_TEXT(i, text) false
inline unsigned x2f_c565(int32_t rgb) { return OP1F_COLOUR((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255); }
@HEADER_C@

static void x2f_paint(void* canvas, unsigned where, unsigned shifted) {
    x2f_where_now = where;
    lanes(canvas, shifted);
    if (x2f_how & (unsigned)OP1F_SHOWN_OFF) x2f_centred(canvas, 240, 170, "off", X2F_KNOB[3], 2);
    @PAGE_EXTRA@
}

@HELP_C@
extern "C" int @P@_input(const Op1fInput* e) OP1F_NAMED("@P@_input");
extern "C" int @P@_input(const Op1fInput* e) { return e->bytes >= 36u ? x2f_help_input(e) : 0; }

extern "C" void @P@_frame(const Op1fFrame* now) OP1F_NAMED("@P@_frame");
extern "C" void @P@_frame(const Op1fFrame* now) {
    if (now->bytes < 40u) return;
    @FRAME_HOOK@
    if (@STAND_IN@ && !x2f_stood) {
        if (op1f_page_stand_in(&@CARD@, @ASK@, 0)) x2f_stood = 1;
        else if (!x2f_said) { x2f_said = 1; op1f_log(2, "x2f/@P@.cpp", 1, "@P@: the base did not take its page"); }
    }
    const unsigned how = x2f_how, where = x2f_where;
    if (now->page == &@CARD@ && now->canvas != 0 && (how & (unsigned)OP1F_SHOWN_UP)) {
        if (x2f_help) x2f_help_paint(now->canvas);
        else x2f_paint(now->canvas, where, now->shift ? 1u : 0u);
    }
}
'''

SYNTH = r'''// Generated by x2f from the Open-XY synth @ID@ (@SRC@). Do not edit: change x2f.py and translate again.
//
// The machine renders 64 frames at 44.1 kHz; open-field's engine block is 128 frames at 88.2 kHz. Each engine
// block runs one machine block and interpolates it 2x (runtime/x2f_rt.h). Pitch: the unit's step is
// 2^32 f / 88200 a frame, the machine's increment 2^32 f / 44100, so the machine is handed 2 x step.
// Notes: BEGINS on a voice that has not played since it was handed over zeroes the machine voice and runs
// initialize + start (the SDK's voice adapter); BEGINS on a sounding voice runs start only (its retrigger).
// ENDS changes nothing in the machine: the unit's envelope owns the release.
#include "x2f_card.h"
#include "x2f_rt.h"
#include "@SOURCE@"

extern "C" const Op1fCard @CARD@ OP1F_NAMED("@CARD@");
extern "C" volatile unsigned @P@_note_on OP1F_NAMED("@P@_note_on");    // counted for the face (its own unit)
extern "C" volatile unsigned @P@_note_off OP1F_NAMED("@P@_note_off");
extern "C" volatile unsigned char @P@_held[128] OP1F_NAMED("@P@_held");   // voices holding each MIDI pitch

namespace {
enum { X2F_VALUES = @NV@, X2F_SHOWN = @NV@, X2F_STEREO = @STEREO@, X2F_FACE = @FACE@ };
// Tuning (x2f's pitch check): the machine is handed step x R/65536 x 2^SHIFT instead of step x 2. TUNED 0: as is.
enum { X2F_TUNED = @TUNED@, X2F_TUNE_R = @TUNE_R@ };
const int X2F_TUNE_SHIFT = @TUNE_SHIFT@;
// The unit's step at the lower edge of each MIDI pitch (a quarter-tone below it), for the screen's held notes.
const uint32_t X2F_NOTE_EDGE[128] = { @NOTE_EDGES@ };
unsigned char x2f_voice_note[64];                 // the pitch + 1 each voice holds, 0 for none

unsigned note_of(uint32_t step) {
    unsigned lo = 0, hi = 128;
    while (hi - lo > 1) { const unsigned mid = (lo + hi) >> 1; if (step >= X2F_NOTE_EDGE[mid]) lo = mid; else hi = mid; }
    return lo;
}
void let_go(unsigned v) {
    if (x2f_voice_note[v]) { const unsigned n = x2f_voice_note[v] - 1u; if (@P@_held[n]) --@P@_held[n]; x2f_voice_note[v] = 0; }
}
uint32_t tuned(uint32_t step) {
    uint32_t scaled = (step >> 16) * (uint32_t)X2F_TUNE_R + (((step & 0xffffu) * (uint32_t)X2F_TUNE_R) >> 16);
    if (X2F_TUNE_SHIFT >= 0) {
        if (X2F_TUNE_SHIFT && scaled >= (1u << (32 - X2F_TUNE_SHIFT))) return 0xffffffffu;
        return scaled << X2F_TUNE_SHIFT;
    }
    return scaled >> -X2F_TUNE_SHIFT;
}
const int32_t X2F_ENUM[X2F_VALUES][@EN@] = { @ENUMS@ };
const unsigned char X2F_ENUM_N[X2F_VALUES] = { @ENUM_NS@ };   // a choice's options (its knob's travel in as many zones)
inline unsigned x2f_zone(int v, unsigned n) { if (v <= 0 || n < 2) return 0; const unsigned z = ((unsigned)v * n) >> 15; return z >= n ? n - 1 : z; }
const char X2F_NAMES[X2F_VALUES][16] = { @NAMES@ };
const char X2F_LABELS[X2F_VALUES][@EN@][16] = { @LABELS@ };
const int32_t X2F_FIXED[@NP@] = { @FIXED@ };     // every machine parameter, at its default (Q31 / detent)
const unsigned X2F_MAP[X2F_VALUES] = { @MAP@ };  // the machine parameter each Field value turns

struct Slot {
    @NS@::Voice voice;
    opxy::u8 model, stored[8];                       // a kit: the played key's model and its own eight controls
    x2f::Upsampler up[1 + X2F_STEREO];
    int32_t block[64 * (1 + X2F_STEREO)];
    uint32_t pitch[64];
    int32_t parameters[@NP@ < 8 ? 8 : @NP@];
    uint32_t started;
};
@NS@::Shared shared;
}
// A kit without pad controls has no drum_pad: this one, found when the machine's is not (the SDK's own way).
namespace x2f_pads { struct Absent {}; template <class V, class S> Absent drum_pad(V&, S&, opxy::u32, const opxy::i32*) { return Absent(); } }
namespace {

inline int32_t amount(int v) {
    if (v <= 0) return 0;
    if (v >= 32767) return 0x7fffffff;
    return v * 65538;
}
}

extern "C" const unsigned x2f_voice_bytes = sizeof(Slot);

extern "C" void @P@_render(const Op1fVoiceBlock* now) OP1F_NAMED("@P@_render");
extern "C" void @P@_render(const Op1fVoiceBlock* now) {
    int* out = now->out;
    const unsigned words = 128u * (1u + X2F_STEREO);
    Slot* s = (Slot*)now->state;
    if (now->bytes < 52u || s == 0) { for (unsigned k = 0; k < words; ++k) out[k] = 0; return; }
    const uint32_t owner = now->voice + 1u;
    if (now->events & (unsigned)OP1F_VOICE_FRESH) {
        if (s->started) @NS@::destroy(s->voice, shared, owner);
        s->started = 0;
        for (unsigned c = 0; c < 1u + X2F_STEREO; ++c) s->up[c].reset();
    }
    if (X2F_FACE) {                                   // what the screen is told: note starts, releases, held pitches
        const unsigned v = now->voice & 63u;
        if (now->events & (unsigned)OP1F_VOICE_FRESH) let_go(v);
        if (now->events & (unsigned)OP1F_VOICE_BEGINS) {
            let_go(v);
            const unsigned n = note_of(now->step);
            x2f_voice_note[v] = (unsigned char)(n + 1u);
            ++@P@_held[n];
            ++@P@_note_on;
        }
        if (now->events & (unsigned)OP1F_VOICE_ENDS) { let_go(v); ++@P@_note_off; }
    }
    if (now->events & (unsigned)OP1F_VOICE_BEGINS) {
        if (!s->started) {
            unsigned char* b = (unsigned char*)&s->voice;
            for (unsigned i = 0; i < sizeof(s->voice); ++i) b[i] = 0;
            @NS@::initialize(s->voice, shared, owner);
            s->started = 1;
        }
        @DRUM_KEY@
        @NS@::start(s->voice, shared, owner);
    }
    if (!s->started) { for (unsigned k = 0; k < words; ++k) out[k] = 0; return; }
    for (unsigned i = 0; i < (unsigned)@NP@; ++i) s->parameters[i] = X2F_FIXED[i];
    for (unsigned i = 0; i < (unsigned)X2F_VALUES; ++i) {
        const int v = now->values[i];
        s->parameters[X2F_MAP[i]] = X2F_ENUM[i][0] < 0 ? amount(v) : X2F_ENUM[i][x2f_zone(v, X2F_ENUM_N[i])];
    }
    const uint32_t increment = X2F_TUNED ? tuned(now->step) : now->step >= 0x80000000u ? 0xffffffffu : now->step << 1;
    for (unsigned i = 0; i < 64; ++i) s->pitch[i] = increment;
    @DRUM_RENDER@@NS@::render(s->voice, shared, owner, s->block, s->parameters, s->pitch);
    if (X2F_STEREO) {
        for (unsigned i = 0; i < 64; ++i) {
            int32_t e, o;
            s->up[0].sample(s->block[2 * i], e, o); out[4 * i] = e; out[4 * i + 2] = o;
            s->up[1].sample(s->block[2 * i + 1], e, o); out[4 * i + 1] = e; out[4 * i + 3] = o;
        }
    } else {
        for (unsigned i = 0; i < 64; ++i) { int32_t e, o; s->up[0].sample(s->block[i], e, o); out[2 * i] = e; out[2 * i + 1] = o; }
    }
}
'''

SAMPLER = r'''// Generated by x2f from the Open-XY sampler @ID@ (@SRC@). Do not edit: change x2f.py and translate again.
//
// The OP-XY hands a sample machine the track's sample. open-field gives a pack no sample of the unit's, so this
// pack has a folder of its own on the unit's disk ("@FOLDER@", seen in the disk mode): the first .wav or .aif in
// it is the sample, read into the pack's working memory at the start and again whenever the disk mode closes.
// WAV (16 or 24 bit) and AIFF (16 or 24 bit, the Field's own recordings among them), mono or stereo, any rate.
// A note hands the machine the sample as it was at the note's start (initialize + start, as the SDK's adapter),
// then render_sample every block: the whole sample as the region, level unity, forward, the eight values as its
// parameters, the pitch as a synth's (2 x step). The machine renders 64 frames at 44.1 kHz, upsampled 2x.
#include "x2f_card.h"
#include "x2f_rt.h"
#include <opxy/sample_engine.hpp>
#include "@SOURCE@"

extern "C" const Op1fCard @CARD@ OP1F_NAMED("@CARD@");
extern "C" volatile unsigned @P@_note_on OP1F_NAMED("@P@_note_on");
extern "C" volatile unsigned @P@_note_off OP1F_NAMED("@P@_note_off");
extern "C" volatile unsigned char @P@_held[128] OP1F_NAMED("@P@_held");
volatile unsigned @P@_note_on, @P@_note_off;
volatile unsigned char @P@_held[128];

namespace {
enum { X2F_VALUES = @NV@, X2F_SHOWN = @NV@, X2F_STEREO = @STEREO@, X2F_MEMORY_ASK = 0, X2F_FOLDER = 0 };   // asks: memory, then the engine
const int32_t X2F_ENUM[X2F_VALUES][@EN@] = { @ENUMS@ };
const unsigned char X2F_ENUM_N[X2F_VALUES] = { @ENUM_NS@ };   // a choice's options (its knob's travel in as many zones)
inline unsigned x2f_zone(int v, unsigned n) { if (v <= 0 || n < 2) return 0; const unsigned z = ((unsigned)v * n) >> 15; return z >= n ? n - 1 : z; }
const char X2F_NAMES[X2F_VALUES][16] = { @NAMES@ };
const char X2F_LABELS[X2F_VALUES][@EN@][16] = { @LABELS@ };
const int32_t X2F_FIXED[@NP@] = { @FIXED@ };
const unsigned X2F_MAP[X2F_VALUES] = { @MAP@ };
const unsigned X2F_SAMPLE_BYTES = @SAMPLE_BYTES@;

// The sample now, published whole by the frame function: a note takes the view at its start.
struct Loaded { const int16_t* data; uint32_t frames, rate, channels; };
Loaded loaded[2];
volatile unsigned loaded_at;                          // which of the two is current, + 1 (0: none)
char sample_name[40];

struct Slot {
    @NS@::Voice voice;
    opxy::SampleView view;
    x2f::Upsampler up[1 + X2F_STEREO];
    int32_t block[64 * (1 + X2F_STEREO)];
    uint32_t pitch[64];
    int32_t parameters[@NP@ < 8 ? 8 : @NP@];
    uint32_t started;
};

inline int32_t amount(int v) {
    if (v <= 0) return 0;
    if (v >= 32767) return 0x7fffffff;
    return v * 65538;
}
inline uint32_t le32(const uint8_t* p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }
inline uint32_t be32(const uint8_t* p) { return ((uint32_t)p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]; }
inline uint32_t be16(const uint8_t* p) { return (p[0] << 8) | p[1]; }

// The rate in an AIFF's 80-bit float, for the rates samples have (8 kHz to 192 kHz).
uint32_t aiff_rate(const uint8_t* p) {
    const int exponent = ((p[0] & 0x7f) << 8 | p[1]) - 16383;
    const uint32_t mantissa = be32(p + 2);
    return exponent < 0 || exponent > 31 ? 0 : mantissa >> (31 - exponent);
}

// The file at `bytes`..`bytes + size` as 16-bit samples, in place: true when it is a WAV or AIFF of 16 or 24 bits.
bool decode(uint8_t* bytes, uint32_t size, Loaded& out) {
    if (size < 12) return false;
    const bool wav = bytes[0] == 'R' && bytes[1] == 'I' && bytes[2] == 'F' && bytes[3] == 'F';
    const bool aiff = bytes[0] == 'F' && bytes[1] == 'O' && bytes[2] == 'R' && bytes[3] == 'M';
    if (!wav && !aiff) return false;
    uint32_t channels = 0, bits = 0, rate = 0, at = 12, data = 0, data_bytes = 0;
    while (at + 8 <= size) {
        const uint8_t* c = bytes + at;
        const uint32_t n = wav ? le32(c + 4) : be32(c + 4);
        const uint8_t* b = c + 8;
        if (wav && c[0] == 'f' && c[1] == 'm' && c[2] == 't' && c[3] == ' ' && n >= 16) {
            if ((b[0] | b[1] << 8) != 1 && (b[0] | b[1] << 8) != 0xfffe) return false;     // PCM only
            channels = b[2] | b[3] << 8; rate = le32(b + 4); bits = b[14] | b[15] << 8;
        } else if (wav && c[0] == 'd' && c[1] == 'a' && c[2] == 't' && c[3] == 'a') {
            data = at + 8; data_bytes = n;
        } else if (aiff && c[0] == 'C' && c[1] == 'O' && c[2] == 'M' && c[3] == 'M' && n >= 18) {
            channels = be16(b); bits = be16(b + 6); rate = aiff_rate(b + 8);
        } else if (aiff && c[0] == 'S' && c[1] == 'S' && c[2] == 'N' && c[3] == 'D' && n >= 8) {
            data = at + 16 + be32(b); data_bytes = n - 8 - be32(b);
        }
        at += 8 + n + (n & 1u);
    }
    if (!data || !channels || channels > 2 || (bits != 16 && bits != 24) || !rate) return false;
    if (data + data_bytes > size) data_bytes = size - data;
    const uint32_t width = bits / 8u, frames = data_bytes / (width * channels);
    int16_t* pcm = (int16_t*)(bytes + ((data + 1u) & ~1u));
    for (uint32_t i = 0; i < frames * channels; ++i) {           // in place, front to back: never ahead of the reading
        const uint8_t* q = bytes + data + i * width;
        pcm[i] = wav ? (int16_t)(q[width - 2] | q[width - 1] << 8) : (int16_t)(q[0] << 8 | q[1]);
    }
    out.data = pcm; out.frames = frames; out.rate = rate; out.channels = channels;
    return true;
}
}

extern "C" const unsigned x2f_voice_bytes = sizeof(Slot);

// On the display thread at every frame: the sample read at the first frame, and again when the disk mode closes.
// Where it stands is said on the page and in the log, whatever it is.
static unsigned x2f_disk_was = 1, x2f_tries, x2f_said_state = 99;
static unsigned x2f_state;     // 0 starting, 1 disk mode open, 2 no memory, 3 no file, 4 too large, 5 not WAV/AIFF, 6 loaded
static const char* const x2f_state_words[] = {
    "starting", "waiting: close the disk mode and it loads", "the unit gave no memory for the sample",
    "no .wav or .aif in its folder @FOLDER@", "the sample is larger than its room", "not a 16 or 24 bit WAV or AIFF", "loaded" };
static void x2f_say(unsigned state) {
    x2f_state = state;
    if (state != x2f_said_state) { x2f_said_state = state; op1f_log(2, "x2f/@P@.cpp", 1, x2f_state_words[state]); }
}
static void x2f_load(const Op1fFrame* now) {
    const unsigned disk = now->bytes >= 40u ? now->disk : 0u;
    if (disk) { x2f_disk_was = 1; x2f_say(1); return; }
    if (!x2f_disk_was) return;                            // read once a start, and once each time the disk mode closes
    uint8_t* memory = (uint8_t*)op1f_memory(&@CARD@, X2F_MEMORY_ASK);
    if (!memory) {                                        // the base clears it over its first frames; refused: said
        if (++x2f_tries > 600) x2f_say(2);                // after ten seconds of asking
        return;
    }
    x2f_disk_was = 0;
    x2f_tries = 0;
    static char names[1024];
    const int listed = op1f_list(&@CARD@, X2F_FOLDER, names, sizeof names);
    const char* pick = 0;
    int at = 0;                                           // op1f_list answers how many names, each with a zero behind
    for (int k = 0; k < listed && at < (int)sizeof names; ++k) {
        const char* n = names + at; int len = 0; while (n[len]) ++len;
        at += len + 1;
        const char* d = n + len - 4;
        if (len > 4 && (((d[1] | 32) == 'w' && (d[2] | 32) == 'a' && (d[3] | 32) == 'v') ||
                        ((d[1] | 32) == 'a' && (d[2] | 32) == 'i' && (d[3] | 32) == 'f') ||
                        (len > 5 && (d[0] | 32) == 'a' && (d[1] | 32) == 'i' && (d[2] | 32) == 'f' && (d[3] | 32) == 'f'))) { pick = n; break; }
    }
    const unsigned next = loaded_at == 1u ? 1u : 0u;      // the half not in use
    loaded_at = 0;                                        // no new note takes a sample while it is read
    if (!pick) { x2f_say(3); sample_name[0] = 0; return; }
    uint8_t* half = memory + next * (X2F_SAMPLE_BYTES / 2u);
    const int got = op1f_read(&@CARD@, X2F_FOLDER, pick, half, X2F_SAMPLE_BYTES / 2u);
    if (got <= 0 || !decode(half, (uint32_t)got, loaded[next])) { x2f_say(got == OP1F_TOO_LARGE ? 4 : 5); sample_name[0] = 0; return; }
    unsigned k = 0; while (pick[k] && k < sizeof sample_name - 1) { sample_name[k] = pick[k]; ++k; } sample_name[k] = 0;
    loaded_at = next + 1u;
    x2f_say(6);
}

extern "C" void @P@_render(const Op1fVoiceBlock* now) OP1F_NAMED("@P@_render");
extern "C" void @P@_render(const Op1fVoiceBlock* now) {
    int* out = now->out;
    const unsigned words = 128u * (1u + X2F_STEREO);
    Slot* s = (Slot*)now->state;
    if (now->bytes < 52u || s == 0) { for (unsigned k = 0; k < words; ++k) out[k] = 0; return; }
    if (now->events & (unsigned)OP1F_VOICE_FRESH) {
        s->started = 0;
        for (unsigned c = 0; c < 1u + X2F_STEREO; ++c) s->up[c].reset();
    }
    if (now->events & (unsigned)OP1F_VOICE_BEGINS) {
        const unsigned at = loaded_at;
        if (at) {
            const Loaded& l = loaded[at - 1u];
            s->view.data = l.data; s->view.frames = l.frames; s->view.rate = l.rate; s->view.channels = l.channels;
            unsigned char* b = (unsigned char*)&s->voice;
            for (unsigned i = 0; i < sizeof(s->voice); ++i) b[i] = 0;
            @NS@::initialize(s->voice, s->view);
            @NS@::start(s->voice);
            s->started = 1;
        } else s->started = 0;
    }
    if (!s->started) { for (unsigned k = 0; k < words; ++k) out[k] = 0; return; }
    for (unsigned i = 0; i < (unsigned)@NP@; ++i) s->parameters[i] = X2F_FIXED[i];
    for (unsigned i = 0; i < (unsigned)X2F_VALUES; ++i) {
        const int v = now->values[i];
        s->parameters[X2F_MAP[i]] = X2F_ENUM[i][0] < 0 ? amount(v) : X2F_ENUM[i][x2f_zone(v, X2F_ENUM_N[i])];
    }
    const uint32_t increment = now->step >= 0x80000000u ? 0xffffffffu : now->step << 1;
    for (unsigned i = 0; i < 64; ++i) s->pitch[i] = increment;
    opxy::SampleBlock b = {};
    b.output = s->block; b.parameters = s->parameters; b.pitch = s->pitch;
    b.transport.bpm_x10 = 0; b.transport.ticks96 = 0; b.transport.playing = now->playing != 0;
    b.region.begin = 0; b.region.end = s->view.frames; b.region.level = opxy::sample_unity_level;
    b.output_channels = 1u + X2F_STEREO;
    @NS@::render_sample(s->voice, b);
    if (X2F_STEREO) {
        for (unsigned i = 0; i < 64; ++i) {
            int32_t e, o;
            s->up[0].sample(s->block[2 * i], e, o); out[4 * i] = e; out[4 * i + 2] = o;
            s->up[1].sample(s->block[2 * i + 1], e, o); out[4 * i + 1] = e; out[4 * i + 3] = o;
        }
    } else {
        for (unsigned i = 0; i < 64; ++i) { int32_t e, o; s->up[0].sample(s->block[i], e, o); out[2 * i] = e; out[2 * i + 1] = o; }
    }
}
'''


EFFECT = r'''// Generated by x2f from the Open-XY effect @ID@ (@SRC@). Do not edit: change x2f.py and translate again.
//
// Rates match: an open-field effect block is up to 64 frames at 44.1 kHz, the machine's is 64 at 44.1 kHz, so
// the machine runs as it is, in place. A shorter block is padded to 64 in working memory.
// Dry: an OP-XY send effect returns only its wet signal and the stock mixer adds the dry by its dryParameter.
// open-field's effect replaces the sound in place, so the dry is added back here at that parameter's level.
#include "x2f_card.h"
#include "x2f_rt.h"
#include <opxy/effect.hpp>
#include <opxy/transport.hpp>
#include "@SOURCE@"

extern "C" const Op1fCard @CARD@ OP1F_NAMED("@CARD@");

namespace {
enum { X2F_VALUES = @NV@, X2F_SHOWN = @NV@, X2F_DRY = @DRY@, X2F_BUFFER_WORDS = @BUFWORDS@ };
const int32_t X2F_ENUM[X2F_VALUES][@EN@] = { @ENUMS@ };
const unsigned char X2F_ENUM_N[X2F_VALUES] = { @ENUM_NS@ };   // a choice's options (its knob's travel in as many zones)
inline unsigned x2f_zone(int v, unsigned n) { if (v <= 0 || n < 2) return 0; const unsigned z = ((unsigned)v * n) >> 15; return z >= n ? n - 1 : z; }
const char X2F_NAMES[X2F_VALUES][16] = { @NAMES@ };
const char X2F_LABELS[X2F_VALUES][@EN@][16] = { @LABELS@ };
const int32_t X2F_FIXED[@NP@] = { @FIXED@ };
const unsigned X2F_MAP[X2F_VALUES] = { @MAP@ };  // the machine parameter each Field value turns

struct Place {
    @NS@::State state;
    uint32_t started;
    int32_t left[64], right[64], dry_left[64], dry_right[64];
    int32_t parameters[@NP@ < 4 ? 4 : @NP@];
    int32_t buffer[X2F_BUFFER_WORDS ? X2F_BUFFER_WORDS : 1];
};

inline int32_t amount(int v) {
    if (v <= 0) return 0;
    if (v >= 32767) return 0x7fffffff;
    return v * 65538;
}
inline int32_t add_held(int32_t a, int32_t b) {
    const int32_t s = (int32_t)((uint32_t)a + (uint32_t)b);
    if (((a ^ s) & (b ^ s)) < 0) return a < 0 ? (int32_t)0x80000000u : 0x7fffffff;
    return s;
}
}

extern "C" const unsigned x2f_state_bytes = sizeof(Place);

// On the display thread before the place's first block (the place passes the sound dry until it returns): the
// machine made new and run on silence for a while, so what its first calls build (a cleared delay line, tables;
// one machine took three million cycles in its first call) is built here, never on the audio thread.
extern "C" void @P@_reset(unsigned place, void* state) OP1F_NAMED("@P@_reset");
extern "C" void @P@_reset(unsigned, void* state) {
    if (state == 0) return;
    Place* p = (Place*)state;
    @NS@::initialize(p->state);
    p->started = 1;
    for (unsigned i = 0; i < (unsigned)@NP@; ++i) p->parameters[i] = X2F_FIXED[i];
    const unsigned x2f_playing = 0;
    for (unsigned b = 0; b < 256u; ++b) {
        for (unsigned i = 0; i < 64; ++i) p->left[i] = p->right[i] = 0;
        @CALL@
    }
}

extern "C" void @P@_process(const Op1fEffectBlock* now) OP1F_NAMED("@P@_process");
extern "C" void @P@_process(const Op1fEffectBlock* now) {
    if (now->bytes < 44u || now->state == 0) return;
    Place* p = (Place*)now->state;
    if (!p->started || now->fresh) { @NS@::initialize(p->state); p->started = 1; }
    for (unsigned i = 0; i < (unsigned)@NP@; ++i) p->parameters[i] = X2F_FIXED[i];
    for (unsigned i = 0; i < (unsigned)X2F_VALUES; ++i) {
        const int v = now->values[i];
        p->parameters[X2F_MAP[i]] = X2F_ENUM[i][0] < 0 ? amount(v) : X2F_ENUM[i][x2f_zone(v, X2F_ENUM_N[i])];
    }
    const unsigned frames = now->frames > 64u ? 64u : now->frames;
    const bool on = now->bytes < 28u || now->on;          // tails: switched off, the base still calls with on 0
    for (unsigned i = 0; i < 64; ++i) {
        const int32_t l = i < frames ? now->left[i] : 0, r = i < frames ? now->right[i] : 0;
        p->left[i] = on ? l : 0; p->dry_left[i] = l;      // off: the machine hears silence, its tail rings on
        p->right[i] = on ? r : 0; p->dry_right[i] = r;
    }
    const unsigned x2f_playing = now->playing;
    @CALL@
    const int32_t dry = X2F_DRY >= 0 ? p->parameters[X2F_DRY < 0 ? 0 : X2F_DRY] : 0;
    for (unsigned i = 0; i < frames; ++i) {
        if (!on) {                                        // off: the sound as it came, and the tail over it
            now->left[i] = add_held(p->dry_left[i], p->left[i]);
            now->right[i] = add_held(p->dry_right[i], p->right[i]);
            continue;
        }
        now->left[i] = X2F_DRY >= 0 ? add_held(p->left[i], opxy::q31::multiply(p->dry_left[i], dry)) : p->left[i];
        now->right[i] = X2F_DRY >= 0 ? add_held(p->right[i], opxy::q31::multiply(p->dry_right[i], dry)) : p->right[i];
    }
}
'''


def c_str(s):
    return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')


def fill(template, mapping):
    for k, v in mapping.items():
        template = template.replace("@%s@" % k, str(v))
    return template


def tables(params, knobs):
    vis = [params[i] for i in knobs]
    en = max([8] + [len(p["enum"]) for p in vis if p["enum"]])     # every choice of the longest list
    enums, names, labels = [], [], []
    for p in vis:
        if p["enum"]:
            row = (p["enum"] + [p["enum"][-1]] * en)[:en]
            lab = [field_name(l, "?") for l in p["labels"]] + [""] * en
            enums.append("{%s}" % ", ".join(str(x) for x in row))
            labels.append("{%s}" % ", ".join(c_str(l) for l in lab[:en]))
        else:
            enums.append("{-1}")
            labels.append('{""}')
        names.append(c_str(p["name"]))
    fixed = []
    for p in params:
        if p["enum"]:
            fixed.append(str(p["enum"][p["index"]]))
        else:
            v = p["start"]
            fixed.append(str(0 if v <= 0 else Q31_MAX if v >= 32767 else v * 65538))
    return {"ENUMS": ", ".join(enums), "NAMES": ", ".join(names), "LABELS": ", ".join(labels),
            "FIXED": ", ".join(fixed), "NV": len(vis), "NP": len(params),
            "MAP": ", ".join(str(i) for i in knobs) or "0", "EN": en,
            "ENUM_NS": ", ".join(str(len(p["enum"]) if p["enum"] else 0) for p in vis) or "0"}


def effect_call(m):
    sig = re.search(r"void\s+process\s*\(([^)]*)\)", m["core"], re.S)
    if not sig:
        raise SystemExit("%s: core.h declares no process()" % m["path"])
    args = []
    for raw in sig.group(1).split(","):
        a = raw.strip()
        if "State" in a:
            args.append("p->state")
        elif "Buffer" in a:
            args.append("buffer")
        elif "Transport" in a:
            args.append("transport")
        elif "Parameters" in a:
            args.append("p->parameters")
        elif "Channel" in a:
            args.append("p->left" if not any(x == "p->left" for x in args) else "p->right")
        else:
            raise SystemExit("%s: process() argument %r is not mapped" % (m["path"], a))
    pre = ""
    if "buffer" in args:
        pre += "opxy::Buffer buffer = {X2F_BUFFER_WORDS ? p->buffer : 0, (uint32_t)X2F_BUFFER_WORDS}; "
    if "transport" in args:
        # Tempo is not handed to open-field effects; the machine's own no-tempo fallback applies.
        pre += "opxy::Transport transport = {0u, 0u, x2f_playing != 0}; "
    return pre + "@NS@::process(%s);" % ", ".join(args)


HELD_PITCHES_FIELD = "    u32 held_pitches[4];     // x2f: held MIDI pitches, a bit each (filled on open-field from note on/off)\n"


def sdk_overlay(m, out_dir):
    """A screen written against a different open-xy SDK than the one x2f points to gets a copy of the SDK headers
    with what it needs added (whole, because the SDK headers include each other by relative path):
      held_pitches  the mda ports read Input.held_pitches (a bit per held MIDI pitch), from an SDK that was never
                    published. Added at the end of Input, filled by x2f from note on/off.
      old Frame     SDK 1.0 screens declare Frame<paths, points, nodes, palette>; today's is Frame<paths, points,
                    nodes, texts, palette>. Frame takes either: four sizes are the old layout with no texts.
    Returns the overlay's include folder, or None when the screen needs nothing."""
    text = ""
    for f in ("display.cpp", "display.h"):
        if os.path.isfile(os.path.join(m["path"], f)):
            text += open(os.path.join(m["path"], f), errors="replace").read()
    src = os.path.join(opxy_include(), "opxy")
    draw_text = open(os.path.join(src, "draw.hpp")).read()
    want_held = "held_pitches" in text and "held_pitches" not in draw_text
    want_frame4 = bool(re.search(r"draw::Frame<\s*\w+\s*,\s*\w+\s*,\s*\w+\s*,\s*\w+\s*>", text)) and \
        "u32 Texts, u32 Palette>\nstruct Frame" in draw_text
    if not (want_held or want_frame4):
        return None
    root = os.path.join(out_dir, "sdk_overlay")
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(src, os.path.join(root, "opxy"))
    draw = os.path.join(root, "opxy", "draw.hpp")
    t = draw_text
    if want_held:
        body = re.search(r"struct Input \{.*?\n\};", t, re.S)
        if not body:
            raise SystemExit("x2f: this open-xy's draw.hpp declares no struct Input to add held_pitches to")
        end = body.end() - 2                          # before the closing brace: every SDK offset stays put
        t = t[:end] + HELD_PITCHES_FIELD + t[end:]
        # The SDK pins Input's size to the OP-XY firmware's; x2f builds Input itself, so the pin grows by the field.
        t = re.sub(r"sizeof\(Input\) == (\d+)", lambda g: "sizeof(Input) == %d" % (int(g.group(1)) + 16), t)
        print("x2f: %s's screen reads held_pitches, which this open-xy does not declare: x2f adds it and fills it" % m["definition"]["id"])
    if want_frame4:
        t = t.replace("template <u32 Paths, u32 Points, u32 Nodes, u32 Texts, u32 Palette>\nstruct Frame {",
                      "template <u32 Paths, u32 Points, u32 Nodes, u32 Texts, u32 Palette>\nstruct FrameSized {", 1)
        anchor = re.search(r"struct FrameSized \{.*?\n\};\n", t, re.S).end()
        t = t[:anchor] + (
            "// x2f: Frame takes SDK 1.0's four sizes (paths, points, nodes, palette) as well as today's five.\n"
            "template <u32 Paths, u32 Points, u32 Nodes, u32 A, u32 B = 0xffffffffu>\n"
            "struct Frame : FrameSized<Paths, Points, Nodes, (B == 0xffffffffu ? 0 : A), (B == 0xffffffffu ? A : B)> {};\n"
        ) + t[anchor:]
        print("x2f: %s's screen uses SDK 1.0's Frame<paths, points, nodes, palette>: x2f accepts it" % m["definition"]["id"])
    open(draw, "w").write(t)
    return root


def note_edges():
    """The unit's step (2^32 f / 88200) at a quarter-tone below each MIDI pitch."""
    return [min(0xffffffff, int(round(440.0 * 2 ** ((n - 69.5) / 12) / 88200 * 4294967296))) for n in range(128)]


def tune_words(ratio):
    """(R, SHIFT) with ratio x 2 = R/65536 x 2^SHIFT, R in [32768, 65535]: the machine gets step x 2 x ratio."""
    if not ratio or abs(ratio - 1.0) < 1e-9:
        return 0, 32768, 1
    total = ratio * 2.0
    shift = math.floor(math.log2(total)) + 1
    r = int(round(total / 2 ** shift * 65536))
    if r >= 65536:
        r, shift = 32768, shift + 1
    return 1, max(32768, r), shift


def parse_setting(p, text):
    """A --set value for parameter p: a fraction 0..1, a percent ('50%'), or one of an enum's labels."""
    t = text.strip().lower()
    if p["enum"]:
        labels = [l.lower() for l in p["labels"]]
        if t in labels:
            return labels.index(t)
        if t.isdigit() and int(t) < len(labels):
            return int(t)
        raise SystemExit("x2f: %s takes one of: %s" % (p["id"], ", ".join(p["labels"])))
    v = float(t[:-1]) / 100 if t.endswith("%") else float(t)
    if not 0 <= v <= 1:
        raise SystemExit("x2f: %s takes 0..1 or 0%%..100%%" % p["id"])
    return int(round(v * 32767))


def choose_knobs(m, params, most, knobs_arg=None, sets_arg=None):
    """Which machine parameters the Field's knobs turn (at most `most`), and the fixed values of the rest.
    Given with --knobs/--set, else remembered for this machine, else: the machine's first ones, leaving out the
    dry control when there are more than fit (the Field adds the dry itself). Both are remembered when given."""
    saved = load_config()
    key = m["path"]
    ids = [p["id"] for p in params]
    if knobs_arg:
        chosen = [k.strip() for k in knobs_arg.split(",") if k.strip()]
        bad = [k for k in chosen if k not in ids]
        if bad or len(chosen) > most or len(set(chosen)) != len(chosen):
            raise SystemExit("x2f: --knobs takes up to %d different ids of: %s" % (most, ", ".join(ids)))
        saved.setdefault("knobs", {})[key] = chosen
    sets = dict(saved.get("sets", {}).get(key, {}))
    for item in sets_arg or []:
        k, _, v = item.partition("=")
        if k.strip() not in ids:
            raise SystemExit("x2f: --set %s: no such control (%s)" % (k, ", ".join(ids)))
        sets[k.strip()] = v.strip()
    if sets_arg:
        saved.setdefault("sets", {})[key] = sets
    if knobs_arg or sets_arg:
        save_config(saved)
    for k, v in sets.items():
        p = params[ids.index(k)]
        if p["enum"]:
            p["index"] = parse_setting(p, v)
            p["start"] = zone_centre(p["index"], len(p["enum"]))
        else:
            p["start"] = parse_setting(p, v)
    chosen = saved.get("knobs", {}).get(key)
    if chosen and all(c in ids for c in chosen):
        knobs = [ids.index(c) for c in chosen]
    elif most == 8 and len(params) > 4 and any(p.get("control") is not None for p in params[4:]):
        # SDK: a synth's first four parameters own the four encoder positions; later ones are alternates that a
        # press cycles through at position "control" (else position i - 4). On the Field, Shift + knob k is
        # position k's alternate: the same physical knob, as on the OP-XY.
        alternates = params[4:]
        at = [(p["control"] if p.get("control") is not None else i) for i, p in enumerate(alternates)]
        shift = []
        for k in range(4):
            shift += [4 + i for i, c in enumerate(at) if c == k][:1]
        shift += [4 + i for i in range(len(alternates)) if 4 + i not in shift]
        knobs = list(range(min(4, len(params)))) + shift[:most - 4]
    else:
        dry = m["machine"].get("dryParameter")
        order = [i for i in range(len(params)) if not (len(params) > most and ids[i] == dry)] + \
                ([ids.index(dry)] if len(params) > most and dry in ids else [])
        knobs = order[:most]
    if len(params) > most or sets:
        fixed = ["%s=%s" % (params[i]["id"], sets.get(params[i]["id"], "default")) for i in range(len(params)) if i not in knobs]
        print("x2f: %s: on the knobs: %s; fixed: %s  (choose: --knobs id,id,... --set id=value; remembered)"
              % (m["definition"]["id"], ", ".join(ids[i] for i in knobs), ", ".join(fixed) or "none"))
    return knobs


def colour_mode(m, arg=None):
    """partial (the default), full or own: --colours for this machine, else what is remembered for it, else the
    default set with `python x2f.py setup --colours MODE`. Given on a build, it is remembered for that machine."""
    saved = load_config()
    if os.environ.get("X2F_COLOURS") in face.COLOUR_MODES:      # the library's per-build choice: not remembered
        return os.environ["X2F_COLOURS"]
    if arg:
        if arg not in face.COLOUR_MODES:
            raise SystemExit("x2f: --colours takes %s" % ", ".join(face.COLOUR_MODES))
        saved.setdefault("colours", {})[m["path"]] = arg
        save_config(saved)
        return arg
    return saved.get("colours", {}).get(m["path"]) or saved.get("colours_default") or "partial"


def translate(machine_dir, out_dir=None, name=None, cycles=None, rides=None, screen=True, tune=None,
              knobs_arg=None, sets_arg=None, colours_arg=None, sample_mb=None):
    m = read_machine(machine_dir)
    ident = m["definition"]["id"]
    name = field_name(name or ident)
    pid = "x2f_" + symbol(name)
    card = "op1f_card_" + symbol(name)              # open-field's card tool names it so (pack_build.py card_symbol)
    out_dir = os.path.abspath(out_dir or os.path.join(HERE, "out", symbol(name)))
    os.makedirs(out_dir, exist_ok=True)
    is_synth = m["kind"] == "synth"
    has_face = False
    overlay = sdk_overlay(m, out_dir) if is_synth else None
    sample_bytes = int(round((sample_mb or 2) * 1048576)) & ~3
    sdk_inc = [overlay] if overlay else []
    tuned_flag, tune_r, tune_shift = tune_words(tune)
    params = params_of(m, 8 if is_synth else 4)
    knobs = choose_knobs(m, params, 8 if is_synth else 4, knobs_arg, sets_arg)
    shown = len(knobs)
    t = tables(params, knobs)
    desc = re.sub(r"[^ -~]", "", m["machine"].get("description") or m["definition"].get("name") or ident)
    help_extra = []
    if not m["sampler"] and any(os.path.isfile(os.path.join(m["path"], w, "display.cpp")) for w in ("", "visual")):
        help_extra.append("Colours: hold all four knobs down and tap shift (own, partial, full, flipped), or Settings > PACKS")
    if m["sampler"]:
        help_extra.append("Its sample: the first .wav or .aif in its folder %s (disk mode). C4 plays it as recorded" % re.sub(r"[^A-Za-z0-9_-]", "_", name)[:15])
    if m["drum"]:
        help_extra.append("A kit: each key from F at the middle octave plays its own drum; the knobs change every key")
    common_help = "".join('x2f_help_lines(canvas, 12, &y, "%s", dim, 11, 456); ' % c_str(h)[1:-1] for h in help_extra)
    common = dict(t, ID=ident, NAME=c_str(name)[1:-1], DESC=c_str(desc)[1:-1], HELP_EXTRA=common_help, SRC=os.path.relpath(m["source"], os.path.dirname(m["path"])), SOURCE=cpath(m["source"]),
                  CARD=card, NS=m["ns"], P=pid)
    # The machine's own screen: display.cpp and panel.svg.fragment beside its entry point or in visual/ (where
    # some keep their artwork). Synths and effects go through the same pipeline.
    face_at = {}

    def probe_face():
        for where in (m["path"], os.path.join(m["path"], "visual")):
            d, p_ = os.path.join(where, "display.cpp"), os.path.join(where, "panel.svg.fragment")
            if os.path.isfile(d) and os.path.isfile(p_):
                break
        else:
            return False
        inc = [os.path.join(HERE, "runtime")] + sdk_inc + [opxy_include(), m["path"], os.path.dirname(d)] + m["includes"]
        try:
            face_at["probe"] = face.probe(cpath(d), m["ns"], t["FIXED"], inc, out_dir, fill, host_compile, EXE,
                                          [m["source"]] + m["extras"], knobs)
        except (subprocess.CalledProcessError, SystemExit):
            print("x2f: SCREEN NOT TRANSLATED for %s: its display.cpp did not build here (see the compiler output "
                  "above); it gets the values page" % ident)
            return False
        face_at.update(display=d, panel=p_, inc=inc)
        return True

    def make_face():
        display, panel = face_at["display"], face_at["panel"]
        nodes, pal, owners = face_at["probe"]
        mode = colour_mode(m, colours_arg)
        owners = face.owners_for(owners, mode)
        parsed = face.parse_panel(panel)
        # full: one colour an object. Parts the machine drew in the same grey (a string's pieces, a body's
        # facets) take the knob that moves most of them, so an object does not split into several colours.
        groups = {}
        for n in owners:
            own = parsed["nodes"].get(n)
            if own:
                groups.setdefault((own["fill"], own["stroke"]), []).append(n)
        for members in groups.values():
            votes = {}
            for n in members:
                k = (owners[n] >> 8) & 3 if owners[n] & (owners[n] - 1) & 0xff else (owners[n] & 0xff).bit_length() - 1
                votes[k] = votes.get(k, 0) + 1
            lead = max(votes, key=votes.get)
            for n in members:
                owners[n] = (owners[n] & 0xff) | (lead << 8) | 0x1000      # 0x1000: take the object's colour in full
        ft = face.tables(parsed, owners, nodes, pal)
        face_cpp = os.path.join(out_dir, pid + "_face.cpp")
        with open(face_cpp, "w") as f:
            draw_in_use = os.path.join(overlay or opxy_include(), "opxy", "draw.hpp")
            held = "for (unsigned i = 0; i < 4; ++i) input.held_pitches[i] = bits[i];" \
                if "held_pitches" in open(draw_in_use).read() else ""      # x2f's copy or open-xy's own
            services, named = face.host_services(m["path"])
            if named:
                print("x2f: %s's screen calls OP-XY host services, answered on open-field: %s" % (ident, ", ".join(named)))
            f.write(fill(face.FACE.replace("@HOST_SERVICES@", services).replace("@HEADER_C@", face.HEADER_C).replace("@HELP_C@", face.HELP_C), dict(common, ASK=0, DISPLAY=cpath(display), HELD_PITCHES=held,
                         MODE=face.COLOUR_MODES_ORDER.index(mode), **ft)))
        print("x2f: screen %s: colours %s; knobs by node %s" % (os.path.basename(face_cpp), mode,
              {n: "".join("btgo"[k] for k in range(4) if o >> k & 1) for n, o in owners.items()}))

    if is_synth and m["sampler"]:
        stereo = 1 if m["definition"].get("resources", {}).get("stereoOutput") else 0
        folder = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:15]
        body = fill(SAMPLER, dict(common, STEREO=stereo, FOLDER=folder, SAMPLE_BYTES=sample_bytes)) + fill(PAGE.replace("@HEADER_C@", face.HEADER_C).replace("@HELP_C@", face.HELP_C), dict(
            common, STAND_IN=1, ASK=1, FRAME_HOOK="x2f_load(now);",
            PAGE_EXTRA='x2f_centred(canvas, 240, 150, x2f_state == 6 ? sample_name : x2f_state_words[x2f_state], '
                       'x2f_state == 6 ? X2F_GREY : X2F_KNOB[3], 1);'))
        has_face = False
    elif is_synth:
        stereo = 1 if m["definition"].get("resources", {}).get("stereoOutput") else 0     # the SDK's own key
        has_face = screen and probe_face()
        drum_key = drum_render = ""
        if m["drum"]:
            # The OP-XY's drum track (sdk/platform drum_voice.cpp.in): each of 24 keys plays its own model and
            # eight controls (drum_defaults), which the track's eight parameters offset by (word >> 24) - 64. On
            # the Field the keys are the keyboard's 24 from F (MIDI 53, the lowest key at the middle octave), and
            # the engine's eight values are the track's eight parameters. The key passed is the MIDI note (53..76),
            # as the SDK's drum track passes handle->key.
            drum_key = ("{ const int k = ((int)note_of(now->step) - 53) %% 24; "
                        "%s::drum_defaults((opxy::u32)(53 + (k < 0 ? k + 24 : k)), &s->model, s->stored); }" % m["ns"])
            drum_render = ("{ for (unsigned i = 0; i < 8; ++i) { int v = s->stored[i] + (int)((uint32_t)(s->parameters[i] < 0 ? 0 : s->parameters[i]) >> 24) - 64; "
                           "v = v < 0 ? 0 : v > 127 ? 127 : v; s->parameters[i] = v == 127 ? 0x7fffffff : (int32_t)((uint32_t)v << 24); } "
                           "%s::drum_select(s->voice, shared, owner, s->model); "
                           "{ using namespace x2f_pads; static const opxy::i32 neutral[8] = {0}; drum_pad(s->voice, shared, owner, neutral); } }\n    " % m["ns"])
        body = fill(SYNTH, dict(common, DRUM_KEY=drum_key, DRUM_RENDER=drum_render, STEREO=stereo, FACE=1 if has_face else 0, TUNED=tuned_flag, TUNE_R=tune_r,
                                TUNE_SHIFT=tune_shift, NOTE_EDGES=", ".join("%du" % e for e in note_edges())))
        if has_face:
            make_face()
        else:
            body = body.replace("X2F_FACE = 1", "X2F_FACE = 0") + fill(PAGE.replace("@HEADER_C@", face.HEADER_C).replace("@HELP_C@", face.HELP_C), dict(common, STAND_IN=1, ASK=0, FRAME_HOOK="", PAGE_EXTRA="")) + \
                "volatile unsigned %s_note_on, %s_note_off;\nvolatile unsigned char %s_held[128];\n" % (pid, pid, pid)
    else:
        dry_id = m["machine"].get("dryParameter")
        dry = next((i for i, p in enumerate(params) if p["id"] == dry_id), -1)
        buf = int(m["definition"].get("resources", {}).get("bufferBytes", 0)) // 4
        body = fill(EFFECT.replace("@CALL@", effect_call(m)), dict(common, DRY=dry, BUFWORDS=buf))
        has_face = screen and probe_face()
        if has_face:                                   # its own screen; an effect hears no notes
            make_face()
            body += "volatile unsigned %s_note_on, %s_note_off;\nvolatile unsigned char %s_held[128];\n" % (pid, pid, pid)
        else:
            body += fill(PAGE.replace("@HEADER_C@", face.HEADER_C).replace("@HELP_C@", face.HELP_C), dict(common, STAND_IN=0, ASK=0, FRAME_HOOK="", PAGE_EXTRA=""))
    cpp = os.path.join(out_dir, pid + ".cpp")
    with open(cpp, "w") as f:
        f.write(body)
    face_file = os.path.join(out_dir, pid + "_face.cpp")
    if not has_face and os.path.exists(face_file):
        os.remove(face_file)
    meta = {"face": face_file if has_face else None, "machine": m["path"], "kind": m["kind"], "name": name, "pid": pid, "card": card, "cpp": cpp,
            "params": [params[i] for i in knobs], "np": len(params), "shown": shown, "cycles": cycles, "out": out_dir,
            "rides": rides or (None if is_synth else guess_rides(m)),
            "stereo": bool(is_synth and m["definition"].get("resources", {}).get("stereoOutput")),
            "include": [os.path.join(HERE, "runtime"), os.path.join(CFG["open_field"], "module")] + sdk_inc +
                       [opxy_include(), m["path"]] + ([os.path.dirname(face_at["display"])] if has_face else []) + m["includes"], "tune": tune,
            "extras": m["extras"] + ([os.path.normpath(os.path.join(opxy_include(), "..", "host", "sample_kernel.cpp"))]
                                     if uses_sample_kernel(m["path"]) else []),    # the host side's kernel (checks only)
            "source": m["source"], "screen": bool(has_face), "args": [machine_dir, out_dir, name, cycles, rides], "ident": ident, "ns": m["ns"], "drum": m["drum"], "sampler": m["sampler"],
            "sample_bytes": sample_bytes if m["sampler"] else 0, "folder": re.sub(r"[^A-Za-z0-9_-]", "_", name)[:15],
            "colours": colour_mode(m) if is_synth else None}
    with open(os.path.join(out_dir, "x2f.json"), "w") as f:
        json.dump(meta, f, indent=1)
    print("x2f: %s %s -> %s" % (m["kind"], ident, cpp))
    return meta


# --------------------------------------------------------------------------------------------- building a pack

def tool(name):
    path = os.path.join(toolchain_dir(), name + EXE)
    if not os.path.isfile(path):
        raise SystemExit("x2f: the Blackfin toolchain is missing (%s). Run: python %s toolchain fetch"
                         % (path, os.path.join(CFG["open_xy"], "sdk.py")))
    return path


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode:
        missing = sorted(set(re.findall(r"fatal error: '?([\w./-]+?)'?:? (?:file not found|No such file or directory)", r.stderr)))
        if any(m.startswith("opxy/") for m in missing):
            raise SystemExit("x2f: not supported on open-field: the machine needs %s, part of an OP-XY SDK that changes "
                             "the OP-XY's own firmware, which no open-xy release has. x2f translates machines, not OP-XY "
                             "firmware changes" % ", ".join(m for m in missing if m.startswith("opxy/")))
        if missing:
            raise SystemExit("x2f: the machine needs %s, which is not in the machine, open-xy or x2f. If you have it, "
                             "point to its folder: --include <folder> (remembered for this machine)" % ", ".join(missing))
        raise subprocess.CalledProcessError(r.returncode, cmd)


def host_compile(sources, exe):
    if not CFG["host_cxx"]:
        raise SystemExit("x2f: no C++ compiler for this computer found; run `python x2f.py setup`")
    run([CFG["host_cxx"], "-std=c++17", "-O1", "-w", "-DOP1F_HOST"] + sources + ["-o", exe])


def pack_build(args):
    """open-field's own tools/pack_build.py, run in this process with its toolchain pointed at open-xy's: no links,
    no copies, the same on Windows, Linux and macOS. It writes only the files named in args."""
    sys.dont_write_bytecode = True
    tools = os.path.join(CFG["open_field"], "tools")
    for p in (tools, CFG["open_xy"]):
        if p not in sys.path:
            sys.path.insert(0, p)
    import module_build, pack_build as pb
    module_build.TOOLCHAIN = toolchain_dir()
    pb.toolchain = tool
    here = os.getcwd()
    os.chdir(CFG["open_field"])                           # it checks that it runs from its repository's top
    try:
        code = pb.main(args)
    except SystemExit as e:
        code = e.code
    finally:
        os.chdir(here)
    if code:
        raise SystemExit("x2f: open-field's pack_build %s failed (above)" % args[0])


def uses_sample_kernel(machine):
    """Does the machine call the SDK's sample kernel (opxy/sample_kernel.hpp: opxy_sample_interpolate, ...crossfade)?"""
    for top, dirs, files in os.walk(machine):
        dirs[:] = [d for d in dirs if d not in ("tests", "benchmarks", "generators")]
        for f in files:
            if f.endswith((".cpp", ".h", ".hpp")) and "sample_kernel.hpp" in open(os.path.join(top, f), errors="replace").read():
                return True
    return False


def native_sources(machine):
    """A machine's hand-written Blackfin routines (native/*.s), which its C++ calls on a firmware build, and the
    SDK's sample kernel (sdk/platform/blackfin/sample_kernel.s) for a machine that calls it."""
    found = [os.path.join(CFG["open_xy"], "sdk", "platform", "blackfin", "sample_kernel.s")] if uses_sample_kernel(machine) else []
    for top, dirs, files in os.walk(machine):
        dirs[:] = [d for d in dirs if d not in ("tests", "benchmarks", "generators")]
        found += [os.path.join(top, f) for f in sorted(files) if f.endswith((".s", ".S"))]
    return found[:1] + sorted(found[1:]) if found and found[0].endswith("sample_kernel.s") else sorted(found)


_BASE_OFFERS = None


def pack_wants(pack):
    """What a pack wants from other packs, as open-field's own pack_build.py check reads it."""
    r = subprocess.run([sys.executable, "-I", os.path.join(CFG["open_field"], "tools", "pack_build.py"), "check",
                        "--pack", os.path.abspath(pack)], capture_output=True, text=True, cwd=CFG["open_field"],
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if r.returncode or "wants:" not in r.stdout:
        raise SystemExit("x2f: open-field's check could not read %s: %s" % (pack, (r.stderr or r.stdout).strip()[-300:]))
    lines = {l.split(":", 1)[0]: l.split(":", 1)[1].strip() for l in r.stdout.splitlines() if l.startswith(("wants:", "offers:"))}
    words = lambda k: [] if lines.get(k, "nothing") == "nothing" else [w.strip() for w in lines[k].split(",")]
    return words("wants"), words("offers")


def unresolved(pack):
    """The loader leaves out a pack that wants a name nobody offers. Every name an x2f pack wants must be one the
    base offers (open-field's builder/base.pack, read by open-field's own check), or the build fails here."""
    global _BASE_OFFERS
    if _BASE_OFFERS is None:
        _BASE_OFFERS = set(pack_wants(os.path.join(CFG["open_field"], "builder", "base.pack"))[1])
    wants, _ = pack_wants(pack)
    missing = sorted(w for w in wants if w not in _BASE_OFFERS)
    if missing:
        os.remove(pack)
        raise SystemExit("x2f: the pack wants names the base does not offer, so the loader would leave it out: %s"
                         % ", ".join(missing))


def size_from_assembly(asm, sym):
    text = open(asm).read()
    hit = re.search(r"%s:\s*\n\s*\.(?:long|4byte)\s+(\d+)" % re.escape("_" + sym), text) or \
        re.search(r"%s:\s*\n\s*\.(?:long|4byte)\s+(\d+)" % re.escape(sym), text)
    if not hit:
        raise SystemExit("x2f: could not read %s from %s" % (sym, asm))
    return int(hit.group(1))


def build(meta, tune_arg=None, checked=False):
    if meta["kind"] == "synth" and not meta.get("drum") and not meta.get("sampler") and not checked:
        ratio = pitch_step(meta, tune_arg)
        a = meta["args"]
        if ratio and abs(ratio - 1) > 1e-6:
            meta = translate(a[0], a[1], a[2], a[3], a[4], screen=bool(meta.get("face")), tune=ratio)
            ref, rper, tr, tper = measure_pitch(meta)
            print("x2f: tuned: A3 now sounds as %.1f Hz (%s)" % (tr, note_name(tr)))
        return build(meta, checked=True)
    out = meta["out"]
    asm = os.path.join(out, meta["pid"] + ".s")
    run([tool("bfin-elf-g++")] + CXX + ["-I" + d for d in meta["include"] + [freestanding_include()]] + ["-S", "-o", asm, meta["cpp"]])
    face_asm = []
    if meta.get("face"):
        face_asm = [os.path.join(out, meta["pid"] + "_face.s")]
        r = subprocess.run([tool("bfin-elf-g++")] + CXX + ["-I" + d for d in meta["include"] + [freestanding_include()]] + ["-S", "-o", face_asm[0], meta["face"]])
        if r.returncode:
            print("x2f: SCREEN NOT TRANSLATED for %s: its display.cpp did not build for the Field (above); "
                  "building it with the values page" % meta["name"])
            a = meta["args"]
            return build(translate(a[0], a[1], a[2], a[3], a[4], screen=False, tune=meta.get("tune")), checked=True)
    extra_asm = []
    for i, src in enumerate(e for e in meta.get("extras", []) if os.sep + "sdk" + os.sep + "host" + os.sep not in e):   # host-only: checks
        extra_asm.append(os.path.join(out, "%s_part%d_%s.s" % (meta["pid"], i, os.path.splitext(os.path.basename(src))[0])))
        run([tool("bfin-elf-g++")] + CXX + ["-DX2F_NO_LIBCALLS"] + ["-I" + d for d in meta["include"] + [freestanding_include()]] +
            ["-S", "-o", extra_asm[-1], src])
    is_synth = meta["kind"] == "synth"
    bytes_ = size_from_assembly(asm, "x2f_voice_bytes" if is_synth else "x2f_state_bytes")
    bytes_ = (bytes_ + 31) & ~31
    params = [{"name": p["name"], "low": p["low"], "high": p["high"], "start": p["start"]} for p in meta["params"][:meta["shown"]]]
    card_s = os.path.join(out, meta["pid"] + "_card.s")
    if is_synth:
        ask = {"kind": "engine", "name": meta["name"], "stereo": meta["stereo"], "cycles": meta["cycles"] or 40000,
               "voice_bytes": bytes_, "voice_align": 32, "render": meta["pid"] + "_render", "reset": None,
               "shown": meta["pid"] + "_shown", "params": params, "sounds": []}
    else:
        ask = {"kind": "effect", "name": meta["name"], "rides": meta["rides"], "mono": False, "tails": True,
               # 84000: the most a call may declare; the base switches an effect off at twice it, and sums it nowhere
               "cycles": meta["cycles"] or 84000, "state_bytes": bytes_, "state_align": 32,
               "process": meta["pid"] + "_process", "reset": meta["pid"] + "_reset", "shown": meta["pid"] + "_shown", "params": params}
    recipe = {"about": "Translated by x2f from the Open-XY %s %s." % (meta["kind"], os.path.basename(meta["machine"])),
              "name": meta["name"], "version": 1, "interface": 1, "build": meta["pid"],
              "sources": [asm] + extra_asm + face_asm + native_sources(meta["machine"]) + [card_s],
              "works_with": [], "serves": [], "exports": [], "pictures": [],
              "card": {"level": 10, "frame": meta["pid"] + "_frame", "pick": None, "pictures": False,
                       "input": meta["pid"] + "_input",
                       "folders": [{"name": meta["folder"], "place": meta["folder"], "shown": True}] if meta.get("sampler") else [],
                       # a sampler's memory ask first: open-field's check reads 16 bytes of every table, and a
                       # memory table at the very end of the body would run past it
                       "asks": ([{"kind": "memory", "name": "sample", "bytes": meta["sample_bytes"], "align": 4}]
                                if meta.get("sampler") else []) + [ask] +
                                # a screen's working memory: ask 1, after the engine (face.py Scratch)
                                ([{"kind": "memory", "name": "screen", "bytes": 131072, "align": 4}] if meta.get("face") else []) +
                                ([{"kind": "options", "name": meta["name"].replace(" ", "-"), "changed": None, "options": [
                           {"name": "colours", "choices": list(face.COLOUR_MODES_ORDER),
                            "first": face.COLOUR_MODES_ORDER.index(meta.get("colours") or "partial")}]}] if meta.get("face") else [])},
              "marks": [], "named": True, "sum": True, "path": None}
    rpath = os.path.join(out, "recipe.json")
    with open(rpath, "w") as f:
        json.dump(recipe, f, indent=2)
    pack_build(["card", "--recipe", rpath, "--out", card_s])
    pack = os.path.join(out, meta["name"].replace(" ", "_") + ".pack")
    if os.path.exists(pack):
        os.remove(pack)
    pack_build(["build3", "--recipe", rpath, "--profile", profile(), "--out", pack, "--build-dir", os.path.join(out, "build")])
    unresolved(pack)
    print("x2f: %s (%d bytes); %s %d bytes" % (pack, os.path.getsize(pack), "voice" if is_synth else "state", bytes_))
    return pack


# ------------------------------------------------------------------------------------------------- the check

HOST = r'''
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
@INCLUDE@
extern "C" const Op1fCard @CARD@ = {};
extern "C" int op1f_param_heard(const Op1fCard*, unsigned, unsigned, unsigned, int*) { return -1; }
extern "C" int op1f_paint_text(void*, int, int, const char*, unsigned, int) { return 0; }
extern "C" void op1f_paint_box(void*, int, int, int, int, unsigned) {}
extern "C" void op1f_paint_mask(void*, int, int, int, int, const unsigned char*, int, unsigned) {}
extern "C" void op1f_paint_line(void*, int, int, int, int, int, unsigned) {}
extern "C" int op1f_page_stand_in(const Op1fCard*, unsigned, void*) { return 1; }
extern "C" void op1f_log(int, const char*, int, const char* f, ...) {}
extern "C" int op1f_option(const Op1fCard*, unsigned) { return -1; }
extern "C" int op1f_option_set(const Op1fCard*, unsigned, unsigned) { return 0; }
static const unsigned char* x2f_test_wav; static unsigned x2f_test_wav_bytes;
extern "C" int op1f_list(const Op1fCard*, unsigned, char* names, unsigned room) { strcpy(names, "test.wav"); names[9] = 0; return 9; }
extern "C" int op1f_read(const Op1fCard*, unsigned, const char*, void* buffer, unsigned room) {
    if (x2f_test_wav_bytes > room) return OP1F_TOO_LARGE; memcpy(buffer, x2f_test_wav, x2f_test_wav_bytes); return (int)x2f_test_wav_bytes; }
extern "C" void* op1f_memory(const Op1fCard*, unsigned) { static void* m = 0; if (!m) m = calloc(1, 8u << 20); return m; }
static double rms(const double* x, int n) { double s = 0; for (int i = 0; i < n; ++i) s += x[i] * x[i]; return sqrt(s / n); }
// Pitch by counting upward zero crossings of a band-limited copy (a gentle low-pass under the fundamental's
// harmonics), over the window.
static double pitch(const double* x, int n, double rate) {
    double lp = 0, prev = 0; int first = -1, last = -1, count = 0;
    for (int i = 0; i < n; ++i) { lp += 0.02 * (x[i] - lp); if (prev <= 0 && lp > 0) { if (first < 0) first = i; last = i; ++count; } prev = lp; }
    return count > 1 ? (count - 1) * rate / (last - first) : 0;
}
int main() {
@MAIN@
}
'''

SYNTH_MAIN = r'''
    const int blocks = 300, n = blocks * 128;   // 0.43 s at 88.2 kHz
    static double y[300 * 128];
    void* state = calloc(1, x2f_voice_bytes + 64);
    int values[8] = {@STARTS@};
    int out[256];
    const double f = 220.0;
    const unsigned step = (unsigned)(f * 4294967296.0 / 88200.0);
    double peak = 0; int bad = 0;
    for (int b = 0; b < blocks; ++b) {
        Op1fVoiceBlock vb = {};
        vb.bytes = 52; vb.voice = 0; vb.frames = 128; vb.rate = 88200; vb.out = out; vb.values = values;
        vb.step = step; vb.velocity = 100 << 24; vb.state = state;
        vb.events = b == 0 ? (OP1F_VOICE_FRESH | OP1F_VOICE_BEGINS) : 0;
        @P@_render(&vb);
        for (int k = 0; k < 128; ++k) { y[b * 128 + k] = out[k] / 2147483648.0; if (fabs(y[b * 128 + k]) > peak) peak = fabs(y[b * 128 + k]); if (y[b*128+k] != y[b*128+k]) bad = 1; }
    }
    double r = rms(y + 8820, n - 8820), p = pitch(y + 8820, n - 8820, 88200.0);
    printf("synth %s: 220 Hz note -> measured %.1f Hz, rms %.3f, peak %.3f\n", "@NAME@", p, r, peak);
    int ok = !bad && r > 0.005 && peak <= 1.0 && fabs(p - f) / f < 0.03;
    FILE* w = fopen("@WAV@", "wb");
    if (w) { for (int i = 0; i < n; ++i) { short s = (short)(y[i] * 32767); fwrite(&s, 2, 1, w); } fclose(w); }
    printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
'''

EFFECT_MAIN = r'''
    const int blocks = 690;                     // 1 s at 44.1 kHz
    void* state = calloc(1, x2f_state_bytes + 64);
    int values[4] = {@STARTS@};
    int left[64], right[64];
    double in_e = 0, out_e = 0, diff_e = 0, peak = 0; int bad = 0;
    FILE* w = fopen("@WAV@", "wb");
    for (int b = 0; b < blocks; ++b) {
        int keep[64];
        for (int k = 0; k < 64; ++k) {
            const double t = (b * 64 + k) / 44100.0;
            left[k] = right[k] = keep[k] = (int)(0.5 * sin(2 * 3.14159265358979 * 440.0 * t) * 2147483647.0);
        }
        Op1fEffectBlock eb = {};
        eb.bytes = 44; eb.frames = 64; eb.left = left; eb.right = right; eb.values = values; eb.on = 1;
        eb.fresh = b == 0; eb.state = state;
        @P@_process(&eb);
        for (int k = 0; k < 64; ++k) {
            const double i = keep[k] / 2147483648.0, o = left[k] / 2147483648.0;
            if (b > 20) { in_e += i * i; out_e += o * o; diff_e += (o - i) * (o - i); }
            if (fabs(o) > peak) peak = fabs(o);
            if (o != o) bad = 1;
            if (w) { short s = (short)(o * 32767); fwrite(&s, 2, 1, w); fwrite(&s, 2, 1, w); }
        }
    }
    if (w) fclose(w);
    printf("effect %s: 440 Hz sine at -6 dBFS in -> out/in level %.2f, changed %.0f%% of the signal, peak %.3f\n",
           "@NAME@", sqrt(out_e / in_e), 100 * sqrt(diff_e / in_e), peak);
    int ok = !bad && out_e > 0.01 * in_e && diff_e > 0.01 * in_e && peak <= 1.0;
    printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
'''


FACE_HOST = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "@FACE@"
extern "C" const Op1fCard @CARD@ = {};
static unsigned short fb[220][480];
static const int starts[] = {@STARTS@};
static void dot(int x, int y, unsigned c) { if (x >= 0 && x < 480 && y >= 0 && y < 220) fb[y][x] = (unsigned short)c; }
extern "C" int op1f_param_heard(const Op1fCard*, unsigned, unsigned, unsigned i, int* out) {
    if (i >= sizeof starts / sizeof starts[0]) return -1; *out = starts[i]; return 0; }
extern "C" int op1f_paint_text(void*, int x, int y, const char* t, unsigned c, int s) {   // a block per letter
    for (int k = 0; t[k]; ++k) for (int yy = 0; yy < 7 * s; ++yy) for (int xx = 0; xx < 5 * s; ++xx)
        if ((yy % (2 * s)) < s || xx % (2 * s) < s) dot(x + k * 6 * s + xx, y + yy, c);
    return 0; }
extern "C" void op1f_paint_box(void*, int x, int y, int w, int h, unsigned c) { for (int j = 0; j < h; ++j) for (int i = 0; i < w; ++i) dot(x + i, y + j, c); }
extern "C" void op1f_paint_mask(void*, int x, int y, int w, int h, const unsigned char* cover, int stride, unsigned c) {
    for (int j = 0; j < h; ++j) for (int i = 0; i < w; ++i) if (cover[j * stride + i] > 110) dot(x + i, y + j, c); }
extern "C" void op1f_paint_line(void*, int x0, int y0, int x1, int y1, int w, unsigned c) {
    int dx = abs(x1 - x0), dy = abs(y1 - y0), n = dx > dy ? dx : dy; if (!n) n = 1;
    for (int k = 0; k <= n; ++k) { int x = x0 + (x1 - x0) * k / n, y = y0 + (y1 - y0) * k / n;
        for (int a = 0; a < w; ++a) for (int b = 0; b < w; ++b) dot(x + a - w / 2, y + b - w / 2, c); } }
extern "C" int op1f_page_stand_in(const Op1fCard*, unsigned, void*) { return 1; }
extern "C" void op1f_log(int, const char*, int, const char*, ...) {}
extern "C" int op1f_option(const Op1fCard*, unsigned) { return getenv("X2F_LOOK") ? atoi(getenv("X2F_LOOK")) : -1; }
extern "C" void* op1f_memory(const Op1fCard*, unsigned) { static void* m = 0; if (!m) m = calloc(1, 131072); return m; }
extern "C" int op1f_option_set(const Op1fCard*, unsigned, unsigned) { return 0; }
int main(int argc, char** argv) {
    @P@_shown(0, OP1F_SHOWN_UP);
    if (getenv("X2F_HELP")) { Op1fInput e = {}; e.bytes = 36; e.what = OP1F_DOWN; e.key = 0; e.mine = 1; @P@_input(&e); }
    Op1fFrame f = {}; f.bytes = 40; f.page = &@CARD@; f.canvas = fb;
    for (int t = 0; t < 40; ++t) {
        if (t == 5 || t == 20) { ++@P@_note_on; @P@_held[t == 5 ? 57 : 64] = 1; }
        f.ms = (unsigned)(t * 16); memset(fb, 0, sizeof fb); @P@_frame(&f);
    }
    FILE* o = fopen(argv[1], "wb"); fprintf(o, "P6 480 220 255\n");
    for (int y = 0; y < 220; ++y) for (int x = 0; x < 480; ++x) { unsigned c = fb[y][x];
        unsigned char px[3] = {(unsigned char)((c >> 11) << 3), (unsigned char)(((c >> 5) & 63) << 2), (unsigned char)((c & 31) << 3)}; fwrite(px, 3, 1, o); }
    fclose(o); return 0;
}
'''


PITCH_HOST = r'''
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
@INCLUDE@
extern "C" const Op1fCard @CARD@ = {};
extern "C" int op1f_param_heard(const Op1fCard*, unsigned, unsigned, unsigned, int*) { return -1; }
extern "C" int op1f_paint_text(void*, int, int, const char*, unsigned, int) { return 0; }
extern "C" void op1f_paint_box(void*, int, int, int, int, unsigned) {}
extern "C" void op1f_paint_mask(void*, int, int, int, int, const unsigned char*, int, unsigned) {}
extern "C" void op1f_paint_line(void*, int, int, int, int, int, unsigned) {}
extern "C" int op1f_page_stand_in(const Op1fCard*, unsigned, void*) { return 1; }
extern "C" void op1f_log(int, const char*, int, const char*, ...) {}
// Fundamental by autocorrelation: the first lag whose correlation reaches 85% of the strongest, so a lag of two
// periods never wins over one. periodicity: the strongest normalised correlation (near 1: one clear pitch).
static void f0(const double* x, int n, double rate, double* hz, double* periodicity) {
    const int lo = (int)(rate / 2000), hi = (int)(rate / 40);
    double e = 0; for (int i = 0; i + hi < n; ++i) e += x[i] * x[i];
    *hz = 0; *periodicity = 0;
    if (e <= 0) return;
    static double c[8192];
    double top = 0;
    for (int L = lo; L <= hi; ++L) { double s = 0; for (int i = 0; i + hi < n; ++i) s += x[i] * x[i + L]; c[L] = s / e; if (c[L] > top) top = c[L]; }
    for (int L = lo + 1; L < hi; ++L)
        if (c[L] >= c[L - 1] && c[L] >= c[L + 1] && c[L] >= 0.85 * top) {
            const double a = c[L - 1], b = c[L], d = c[L + 1], den = a - 2 * b + d;
            const double shift = den != 0 ? 0.5 * (a - d) / den : 0;
            *hz = rate / (L + shift); *periodicity = top; return;
        }
}
int main() {
    const double f = 220.0;
    // The original, as the OP-XY SDK plays it: 44.1 kHz, increment round(f / 44100 x 2^32), the same parameters.
    static double ref[44100], tr[88200];
    {
        static @NS@::Voice v; static @NS@::Shared sh; memset(&v, 0, sizeof v);
        static int32_t block[128]; static uint32_t pitch[64]; static int32_t params[@NP@ < 8 ? 8 : @NP@];
        for (int i = 0; i < @NP@; ++i) params[i] = X2F_FIXED[i];
        for (int i = 0; i < 64; ++i) pitch[i] = (uint32_t)llround(f / 44100.0 * 4294967296.0);
        @NS@::initialize(v, sh, 1); @NS@::start(v, sh, 1);
        for (int b = 0; b < 689; ++b) {
            @NS@::render(v, sh, 1, block, params, pitch);
            for (int k = 0; k < 64; ++k) ref[b * 64 + k] = block[k * (1 + X2F_STEREO)] / 2147483648.0;
        }
    }
    {   // the translation, as open-field plays it
        void* state = calloc(1, x2f_voice_bytes + 64);
        int values[8] = {@STARTS@}, out[256];
        const unsigned step = (unsigned)llround(f / 88200.0 * 4294967296.0);
        for (int b = 0; b < 689; ++b) {
            Op1fVoiceBlock vb = {}; vb.bytes = 52; vb.frames = 128; vb.rate = 88200; vb.out = out; vb.values = values;
            vb.step = step; vb.velocity = 100 << 24; vb.state = state;
            vb.events = b == 0 ? (OP1F_VOICE_FRESH | OP1F_VOICE_BEGINS) : 0;
            @P@_render(&vb);
            for (int k = 0; k < 128; ++k) tr[b * 128 + k] = out[k * (1 + X2F_STEREO)] / 2147483648.0;
        }
    }
    {   // both, to listen to: the original at 44.1 kHz, the translation at 88.2 kHz (16-bit mono raw)
        FILE* o = fopen("@OUT@/original-A3.raw", "wb"); FILE* t = fopen("@OUT@/translated-A3.raw", "wb");
        for (int i = 0; o && i < 44100; ++i) { double v = ref[i] * 32767; short x = (short)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); fwrite(&x, 2, 1, o); }
        for (int i = 0; t && i < 88200; ++i) { double v = tr[i] * 32767; short x = (short)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); fwrite(&x, 2, 1, t); }
        if (o) fclose(o);
        if (t) fclose(t);
    }
    double rh, rp, th, tp;
    f0(ref + 4410, 8192, 44100.0, &rh, &rp);
    f0(tr + 8820, 16384, 88200.0, &th, &tp);
    printf("X2F_PITCH %.3f %.3f %.3f %.3f\n", rh, rp, th, tp);
    return 0;
}
'''

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(hz):
    if hz <= 0:
        return "?"
    m = 69 + 12 * math.log2(hz / 440.0)
    n = int(round(m))
    return "%s%d%+.0fc" % (NOTE_NAMES[n % 12], n // 12 - 1, (m - n) * 100) if abs(m - n) >= 0.05 else "%s%d" % (NOTE_NAMES[n % 12], n // 12 - 1)


def parse_pitch(text):
    """'A3', 'C#4', 'Bb2', '261.63', '261.63hz' -> Hz (A4 = 440, MIDI 69)."""
    t = text.strip().lower().replace(" ", "")
    m = re.fullmatch(r"([a-g])([#b]?)(-?\d)", t)
    if m:
        n = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}[m.group(1)] + {"#": 1, "b": -1, "": 0}[m.group(2)]
        midi = (int(m.group(3)) + 1) * 12 + n
        return 440.0 * 2 ** ((midi - 69) / 12)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(hz)?", t)
    if m and float(m.group(1)) > 0:
        return float(m.group(1))
    return None


def measure_pitch(meta):
    """(original Hz, its periodicity, translated Hz, its periodicity) for A3 at the machine's defaults."""
    out = meta["out"]
    starts = ", ".join(str(p["start"]) for p in meta["params"][:meta["shown"]]) or "0"
    inc = '#include "%s"\nvolatile unsigned %s_note_on, %s_note_off;\nvolatile unsigned char %s_held[128];' % (
        cpath(meta["cpp"]), meta["pid"], meta["pid"], meta["pid"]) if meta.get("face") else '#include "%s"' % cpath(meta["cpp"])
    src = os.path.join(out, "pitch_check.cpp")
    with open(src, "w") as f:
        f.write(fill(PITCH_HOST, {"INCLUDE": inc, "CARD": meta["card"], "NS": meta["ns"], "NP": meta["np"],
                                  "STARTS": starts, "P": meta["pid"], "OUT": cpath(out)}))
    exe = os.path.join(out, "pitch_check" + EXE)
    host_compile(["-I" + d for d in meta["include"]] + [src] + meta.get("extras", []), exe)
    line = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.split()
    for name, rate in (("original-A3", 44100), ("translated-A3", 88200)):
        raw = os.path.join(out, name + ".raw")
        if os.path.exists(raw):
            raw_to_wav(raw, os.path.join(out, name + ".wav"), rate, 1)
    return tuple(float(v) for v in line[1:5])


def pitch_step(meta, tune_arg=None):
    """Check the synth's pitch and settle its tuning: returns the ratio to translate with (None: as it is).
    A choice is kept as the pitch A3 should sound ("as original", "A3 = 220", a note or Hz), so the ratio is always
    worked out from what the translation plays. X2F_TUNE (the library's) applies without being remembered."""
    saved = load_config()
    tunings = saved.setdefault("tuning", {})
    key = meta["machine"]
    ref, rper, tr, tper = measure_pitch(meta)
    name = meta["ident"]
    print("x2f: pitch of %s at A3 (220 Hz): original (SDK) %s, translated %s" % (
        name, "%.1f Hz %s" % (ref, note_name(ref)) if rper >= 0.6 else "no single pitch",
        "%.1f Hz %s" % (tr, note_name(tr)) if tper >= 0.6 else "no single pitch"))
    if tper < 0.6:
        return None                                   # no pitch to correct against
    mismatch = rper >= 0.6 and abs(1200 * math.log2(tr / ref)) > 15
    own_off = rper >= 0.6 and abs(1200 * math.log2(ref / 220.0)) > 15
    if mismatch:
        print("x2f: TRANSLATION CHANGES PITCH for %s: %.1f Hz in the original, %.1f Hz translated (%+.0f cents)."
              % (name, ref, tr, 1200 * math.log2(tr / ref)))
    elif own_off:
        print("x2f: %s plays A3 as %.1f Hz (%s, %+.0f cents from 220 Hz). The original does the same, so this is the "
              "machine's own tuning, not the translation." % (name, ref, note_name(ref), 1200 * math.log2(ref / 220.0)))
    choice = os.environ.get("X2F_TUNE") or tune_arg
    if not choice and key in tunings:
        choice = tunings[key]["as"]
        print("x2f: tuning remembered for %s: %s (--tune keep|match|fix|<note or Hz> changes it)" % (name, choice))
    if not choice and (mismatch or own_off):
        if not sys.stdin.isatty():
            print("x2f: left as it is. To change it: --tune match (as the original plays)%s, --tune fix (A3 = 220 Hz) "
                  "or --tune <note or Hz>" % ("" if mismatch else ": nothing to change"))
            return None
        print("  Reference: A2 = 110 Hz, A3 = 220 Hz, C4 (middle C) = 261.63 Hz, A4 = 440 Hz; an octave doubles the Hz.")
        while True:
            answer = prompt("  %s[k]eep as it is, [f]ix so A3 = 220 Hz, or type what A3 should sound as "
                            "(a note like C4 or Bb2, or Hz like 261.63): " % ("[m]atch the original (%.1f Hz), " % ref if mismatch else "")).strip()
            if answer.lower() in ("", "k", "keep", "f", "fix", "m", "match") or parse_pitch(answer):
                choice = answer or "keep"
                break
            print("  Not a note or Hz.")
    if not choice:
        return None
    c = choice.strip().lower()
    if c in ("k", "keep") or c.startswith("keep"):
        target, label = None, "keep"
    elif c in ("m", "match") or c.startswith("match"):
        target, label = (ref if rper >= 0.6 else None), "match"
    elif c in ("f", "fix") or c.startswith("fix"):
        target, label = 220.0, "fix"
    else:
        target = parse_pitch(choice)
        if not target:
            raise SystemExit("x2f: --tune %r is not keep, match, fix, a note (A3, C#4, Bb2) or Hz" % choice)
        label = choice
    if not os.environ.get("X2F_TUNE") and (tune_arg or key not in tunings or tunings[key]["as"] != label):
        tunings[key] = {"as": label}
        save_config(saved)
        print("x2f: tuning for %s: %s; remembered" % (name, label))
    return target / tr if target else None


def face_render(meta):
    """The translated screen on the host, 40 frames with two notes, as a 480x220 picture to look at."""
    starts = ", ".join(str(p["start"]) for p in meta["params"][:meta["shown"]])
    src = os.path.join(meta["out"], "face_host.cpp")
    with open(src, "w") as f:
        f.write(fill(FACE_HOST, {"FACE": cpath(meta["face"]), "CARD": meta["card"], "P": meta["pid"], "STARTS": starts}))
    exe = os.path.join(meta["out"], "face_host" + EXE)
    host_compile(["-I" + d for d in meta["include"]] + [src, meta["source"]] + meta.get("extras", []), exe)
    ppm = os.path.join(meta["out"], meta["pid"] + "_screen.ppm")
    subprocess.run([exe, ppm], check=True)
    png = ppm[:-4] + ".png"
    ppm_to_png(ppm, png)
    os.remove(ppm)
    print("x2f: screen picture: %s" % png)


def ppm_to_png(ppm, png):
    with open(ppm, "rb") as f:
        data = f.read()
    head, w, h, rest = data.split(maxsplit=3)[0], 480, 220, data.split(b"\n", 1)[1]
    raw = b"".join(b"\x00" + rest[y * w * 3:(y + 1) * w * 3] for y in range(h))
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    with open(png, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def raw_to_wav(raw, path, rate, channels):
    with open(raw, "rb") as f:
        frames = f.read()
    with wave.open(path, "wb") as w:
        w.setnchannels(channels); w.setsampwidth(2); w.setframerate(rate); w.writeframes(frames)
    os.remove(raw)


SAMPLER_MAIN = r'''
    // A 440 Hz sine, 1 s, 16-bit mono WAV at 44.1 kHz, as the pack's folder would hold it; then C4 is played.
    static unsigned char wav[44 + 88200];
    memcpy(wav, "RIFF", 4); unsigned n = 36 + 88200; memcpy(wav + 4, &n, 4); memcpy(wav + 8, "WAVEfmt ", 8);
    unsigned f16 = 16, rate = 44100, brate = 88200; unsigned short pcm = 1, ch = 1, align = 2, bits = 16;
    memcpy(wav + 16, &f16, 4); memcpy(wav + 20, &pcm, 2); memcpy(wav + 22, &ch, 2); memcpy(wav + 24, &rate, 4);
    memcpy(wav + 28, &brate, 4); memcpy(wav + 32, &align, 2); memcpy(wav + 34, &bits, 2); memcpy(wav + 36, "data", 4);
    unsigned db = 88200; memcpy(wav + 40, &db, 4);
    for (int i = 0; i < 44100; ++i) { short v = (short)(16000 * sin(2 * 3.14159265358979 * 440.0 * i / 44100.0)); memcpy(wav + 44 + 2 * i, &v, 2); }
    x2f_test_wav = wav; x2f_test_wav_bytes = sizeof wav;
    if (getenv("X2F_TEST_SAMPLE")) {                      // a real file in place of the sine: the decoder as it is
        FILE* tf = fopen(getenv("X2F_TEST_SAMPLE"), "rb"); static unsigned char file[4u << 20]; size_t got = tf ? fread(file, 1, sizeof file, tf) : 0;
        if (tf) fclose(tf);
        x2f_test_wav = file; x2f_test_wav_bytes = (unsigned)got;
    }
    Op1fFrame fr = {}; fr.bytes = 40; @P@_frame(&fr);
    const int blocks = 300, total = blocks * 128;
    static double y[300 * 128];
    void* state = calloc(1, x2f_voice_bytes + 64);
    int values[8] = {@STARTS@}, out[256];
    const unsigned step = (unsigned)(261.6256 * 4294967296.0 / 88200.0);
    double peak = 0, e = 0; int bad = 0;
    for (int b = 0; b < blocks; ++b) {
        Op1fVoiceBlock vb = {}; vb.bytes = 52; vb.frames = 128; vb.out = out; vb.values = values; vb.step = step; vb.state = state;
        vb.events = b == 0 ? (OP1F_VOICE_FRESH | OP1F_VOICE_BEGINS) : 0;
        @P@_render(&vb);
        for (int k = 0; k < 128; ++k) { const int w = out[k * (1 + X2F_STEREO)]; y[b * 128 + k] = w / 2147483648.0; if (fabs(y[b*128+k]) > peak) peak = fabs(y[b*128+k]); e += y[b*128+k] * y[b*128+k]; if (y[b*128+k] != y[b*128+k]) bad = 1; }
    }
    double r = rms(y + 8820, total - 8820), p = pitch(y + 8820, total - 8820, 88200.0);
    printf("sampler %s: %s, C4 played -> %.1f Hz, rms %.3f, peak %.3f\n", "@NAME@", getenv("X2F_TEST_SAMPLE") ? getenv("X2F_TEST_SAMPLE") : "a 440 Hz sine", p, r, peak);
    FILE* w = fopen("@WAV@", "wb");
    if (w) { for (int i = 0; i < total; ++i) { short x = (short)(y[i] * 32767); fwrite(&x, 2, 1, w); } fclose(w); }
    int ok = !bad && r > 0.002 && peak <= 1.0;
    printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
'''


def host_run(meta):
    out = meta["out"]
    is_synth = meta["kind"] == "synth"
    starts = ", ".join(str(p["start"]) for p in meta["params"][:meta["shown"]]) or "0"
    wav = os.path.join(out, meta["pid"] + ".raw")
    wav_c = cpath(wav)
    main = fill(SAMPLER_MAIN if meta.get("sampler") else SYNTH_MAIN if is_synth else EFFECT_MAIN, {"STARTS": starts, "P": meta["pid"], "NAME": meta["name"], "WAV": wav_c})
    if meta.get("face"):
        face_render(meta)
    src = fill(HOST, {"INCLUDE": '#include "%s"\nvolatile unsigned %s_note_on, %s_note_off;\nvolatile unsigned char %s_held[128];' % (cpath(meta["cpp"]), meta["pid"], meta["pid"], meta["pid"]) if meta.get("face") else '#include "%s"' % cpath(meta["cpp"]), "CARD": meta["card"], "MAIN": main})
    host_cpp = os.path.join(out, "host_check.cpp")
    with open(host_cpp, "w") as f:
        f.write(src)
    exe = os.path.join(out, "host_check" + EXE)
    host_compile(["-I" + d for d in meta["include"]] + [host_cpp] + meta.get("extras", []), exe)
    r = subprocess.run([exe])
    listen = wav[:-4] + ".wav"
    raw_to_wav(wav, listen, 88200 if is_synth else 44100, 1 if is_synth else 2)
    print("x2f: listen: %s" % listen)
    return r.returncode == 0


def check():
    core = CFG["machines"]
    ok = True
    for machine in ("karplus", "ringmod"):
        meta = translate(os.path.join(core, machine))
        ok = host_run(meta) and ok
        build(meta)
    print("x2f check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def setup(flags):
    """Point x2f at everything (asking for what no flag gave), check each, remember them."""
    saved = load_config()
    for key in SETTINGS:
        value = flags.get(key)
        if value:
            problem = valid(key, value)
            if problem:
                raise SystemExit("x2f: %s: %s" % (SETTINGS[key][1], problem))
        elif sys.stdin.isatty():
            current = saved.get(key)
            if current and valid(key, current) is None:
                keep = prompt("\n%s: %s. Keep it? [Y/n] " % (SETTINGS[key][1], current)).strip().lower()
                if keep in ("", "y", "yes"):
                    continue
            value = ask(key, "remembered after this") if key != "machines" or \
                prompt("\nAlso point to Open-XY machines for `check`? [Y/n] ").strip().lower() in ("", "y", "yes") else None
        if value:
            saved[key] = value if key == "host_cxx" else absolute(value)
    save_config(saved)
    print("\nx2f: remembered in %s" % CONFIG_FILE)
    return where()


def where():
    """What x2f remembers, and whether each still holds."""
    saved = load_config()
    if not saved:
        print("x2f remembers nothing yet: python x2f.py setup (or just run a command; it will ask).")
        return 1
    problems = 0
    for key, url in saved.get("repos", {}).items():
        print("  %-10s cloned from %s" % (key, url))
    for key, (flag, what, _) in SETTINGS.items():
        value = saved.get(key)
        problem = valid(key, value)
        problems += bool(problem and key in NEEDS["build"])
        print("  %-10s %s\n             %s" % (key, value or "(not set)", "ok" if not problem else
                                               "NEEDS UPDATING: %s -> python x2f.py setup %s <...>" % (problem, flag)))
    return 1 if problems else 0


def reset(keys):
    saved = load_config()
    for key in keys or list(saved):
        saved.pop(key, None)
        saved.get("repos", {}).pop(key, None)
    if saved:
        save_config(saved)
    elif os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)
    print("x2f: forgot %s." % (", ".join(keys) if keys else "everything"))
    return 0


def clone_command(key, into, url):
    """git clone one requirement (open-xy: and its toolchain) and remember where it is."""
    if not into and not sys.stdin.isatty():
        raise SystemExit("x2f: give --into <folder>")
    value = clone(key, into, url)
    problem = valid(key, value)
    if problem:
        raise SystemExit("x2f: cloning %s did not give what x2f needs: %s" % (key, problem))
    saved = load_config()
    saved[key] = value
    save_config(saved)
    print("x2f: %s is at %s (from %s), remembered." % (key, value, saved["repos"][key]))
    return 0


# open-field's limits (module/build_r6e_card.h, docs/packs.md): the loader's room, packs, engines, effects.
ROOM_BYTES, PACKS_MOST, ENGINES_MOST, EFFECTS_MOST = 2031616, 32, 8, 16
ASK_KINDS = {1: "sequencer", 2: "notes heard", 3: "sound stage", 4: "working memory", 5: "pictures in place",
             6: "values kept", 7: "effect", 8: "synth engine", 10: "settings"}


def read_pack(path):
    """What a .pack file holds: name, the bytes it takes in the loader's room, and its asks (kind, name)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"OP1FPACK":
        raise SystemExit("x2f: %s is not an open-field pack" % path)
    word = lambda at: struct.unpack_from("<I", data, at)[0]
    head, link, body, zeroed, lists = word(12), word(20), word(24), word(32), word(64)
    name = data[40:56].split(b"\0")[0].decode("ascii", "replace")
    asks = []
    card = word(92)
    if card and word(head + card + 4) >= 64 and word(head + card + 8) >= 4:   # a card of level 4 or later has asks
        at = head + card
        count, table = word(at + 60), word(at + 56)
        if not (link <= table < link + body):
            count = 0
        for k in range(min(count, 16)):
            kind, _, what = struct.unpack_from("<III", data, head + (table - link) + 12 * k)
            if not (link <= what < link + body):
                asks.append((ASK_KINDS.get(kind, "kind %d" % kind), ""))
                continue
            label = data[head + (what - link) + 4: head + (what - link) + 20].split(b"\0")[0].decode("ascii", "replace") \
                if kind in (1, 7, 8) else ""
            asks.append((ASK_KINDS.get(kind, "kind %d" % kind), label))
    room = (body + lists + zeroed + 31) & ~31
    return {"file": os.path.basename(path), "name": name, "room": room, "asks": asks}


def fit(paths, as_json=False):
    """Will these packs (files, or folders of them) all run together on open-field? The loader places packs in
    the order of their file names, each behind the last, until the room is full; the rest are left out."""
    files = []
    for p in paths:
        p = absolute(p)
        files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.endswith(".pack")) if os.path.isdir(p) else [p]
    packs = sorted((read_pack(f) for f in files), key=lambda r: r["file"])
    for r, f in zip(sorted(packs, key=lambda r: r["file"]), sorted(files, key=os.path.basename)):
        r["path"] = f
    if as_json:
        real_print = print
        globals()["print"] = lambda *a, **k: None
    try:
        return _fit(packs, as_json)
    finally:
        if as_json:
            globals()["print"] = real_print


def _fit(packs, as_json):
    used, engines, effects, out = 0, 0, 0, []
    print("%-24s %-16s %10s  %s" % ("file", "pack", "room", "what it adds"))
    for r in packs:
        left = used + r["room"] > ROOM_BYTES or len(packs) > PACKS_MOST and packs.index(r) >= PACKS_MOST
        what = ", ".join("%s %s" % (k, n) if n else k for k, n in r["asks"]) or "(no asks: base or a firmware change)"
        print("%-24s %-16s %10s  %s%s" % (r["file"], r["name"], "{:,}".format(r["room"]), what,
                                          "   <- LEFT OUT: no room" if left else ""))
        if left:
            out.append(r["file"])
            continue
        used += r["room"]
        engines += sum(1 for k, _ in r["asks"] if k == "synth engine")
        effects += sum(1 for k, _ in r["asks"] if k == "effect")
    print("\npacks %d of %d | room %s of %s bytes (%.0f%%) | synth engines %d of %d | effects %d of %d"
          % (len(packs), PACKS_MOST, "{:,}".format(used), "{:,}".format(ROOM_BYTES), 100.0 * used / ROOM_BYTES,
             engines, ENGINES_MOST, effects, EFFECTS_MOST))
    problems = []
    if len(packs) > PACKS_MOST: problems.append("more than %d packs" % PACKS_MOST)
    if engines > ENGINES_MOST: problems.append("more than %d synth engines (the later ones by name are left out)" % ENGINES_MOST)
    if effects > EFFECTS_MOST: problems.append("more than %d effects" % EFFECTS_MOST)
    if out: problems.append("no room for: " + ", ".join(out))
    if not any(r["file"] == "base.pack" for r in packs): problems.append("no base.pack: every pack needs it")
    print("fits" if not problems else "does not fit: " + "; ".join(problems))
    if as_json:
        sys.stdout.write(json.dumps({"packs": packs, "used": used, "room": ROOM_BYTES, "engines": engines,
                                     "effects": effects, "limits": {"packs": PACKS_MOST, "engines": ENGINES_MOST,
                                     "effects": EFFECTS_MOST}, "left_out": out, "problems": problems}) + "\n")
    return 1 if problems else 0


def library(roots, skip, out, jobs, modes=("partial",)):
    """Every machine under `roots` built into out/packs/x2f-<name>.pack, and out/library.json: each pack's name,
    kind, room, source, screen, pitch and knob notes, or why a machine has no pack. Builds run without asking:
    remembered tunings and knob choices apply; machines never asked about keep the original's tuning."""
    from concurrent.futures import ThreadPoolExecutor
    out = absolute(out)
    packs_dir = os.path.join(out, "packs")
    building = packs_dir + ".new"   # built beside the old packs and swapped in at the end: the old set stays usable
    shutil.rmtree(building, ignore_errors=True)
    os.makedirs(building)
    found = []
    for root in roots:
        root = absolute(root)
        for top, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in skip and d not in ("tests", "node_modules", ".git"))
            if "machine.json" in files and "definition.json" in files:
                found.append((root, top))
    print("x2f library: %d machines under %s" % (len(found), ", ".join(roots)))

    def one(item):
        root, path, mode = item
        rel = os.path.relpath(path, root)
        slug = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(root) + "_" + rel)
        try:
            kind = json.load(open(os.path.join(path, "machine.json"))).get("kind")
        except ValueError:
            kind = None
        entry = {"source": os.path.join(os.path.basename(root), rel), "kind": kind}
        if kind not in ("synth", "effect", "drum", "sample"):
            entry.update(status="not translated", reason="%s machines are not translated yet" % kind)
            return entry
        if kind == "effect" and not os.environ.get("X2F_EFFECTS"):
            # Paused in the library: on the unit effect screens took 0.36 s a frame (comet, hkplate) and most did
            # not load. `x2f.py build <effect>` still translates one; X2F_EFFECTS=1 puts them back in the library.
            entry.update(status="not translated", reason="effects are paused in the library (X2F_EFFECTS=1 builds them)")
            return entry
        if kind == "sample" and not os.environ.get("X2F_SAMPLERS"):
            # Paused too: on the unit the sample engines showed only their file name and sounded wrong. The first wave
            # is the synth engines. `x2f.py build <sampler>` still translates one; X2F_SAMPLERS=1 builds them all.
            entry.update(status="not translated", reason="sample engines are paused in the library (X2F_SAMPLERS=1 builds them)")
            return entry
        env = dict(os.environ)
        r = subprocess.run([sys.executable, os.path.join(HERE, "x2f.py"), "build", path, "--out",
                            os.path.join(out, "build", slug)], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
        log = r.stdout + r.stderr
        lines = [l for l in log.splitlines() if l.startswith("x2f:")]
        if r.returncode and re.search(r": no (engine|fx|sample)\.cpp", log):
            entry.update(status="no sound source", reason="the folder has no engine.cpp / fx.cpp (a design or a template)")
            return entry
        if r.returncode:
            tail = [l for l in log.splitlines() if l.strip()][-1:] or ["build failed"]
            lines = lines or [tail[0] if tail[0].startswith("x2f:") else "x2f: " + tail[0]]
            reason = next((l[5:] for l in reversed(lines) if "needs" in l or "no " in l), None) or \
                     (lines[-1][5:] if lines else "build failed")
            entry.update(status="not supported" if "not supported on open-field" in reason else "failed", reason=reason.strip())
            return entry
        built = [os.path.join(out, "build", slug, f) for f in os.listdir(os.path.join(out, "build", slug)) if f.endswith(".pack")]
        meta = json.load(open(os.path.join(out, "build", slug, "x2f.json")))
        pick = lambda word: next((l[5:].strip() for l in lines if word in l), None)
        entry.update(status="ok", built=built[0], name=meta["name"], screen=bool(meta.get("screen")), drum=bool(meta.get("drum")),
                     sampler=bool(meta.get("sampler")), folder=meta.get("folder") if meta.get("sampler") else None,
                     notes_pitch=None,
                     colours="switch" if meta.get("screen") else None,
                     pitch=pick("pitch of"), tuning=pick("plays A3 as"),
                     knobs=pick("on the knobs"), notes=[l[5:].strip() for l in lines if "SCREEN NOT" in l or "TRANSLATION CHANGES" in l])
        if entry["tuning"] and not entry["tuning"].startswith(meta["ident"] + " plays"):
            entry["tuning"] = None                    # only the machine's own-tuning line, not a remembered choice
        return entry

    # Machines with a screen are built once per colour mode; the rest (effects, values pages) once.
    def has_screen(path):
        return os.path.isfile(os.path.join(path, "display.cpp")) and os.path.isfile(os.path.join(path, "panel.svg.fragment"))
    work = [(root, path, None) for root, path in found]     # every look is in each pack (its colours option)
    with ThreadPoolExecutor(max(1, jobs)) as pool:
        entries = list(pool.map(one, work))
    # A second pack, "<name> fix", for each synth whose pitch was flagged: the translation matched to the original,
    # or a machine tuned off A3 = 220 Hz set to it. Its own name, so it can sit beside the straight port.
    fixes = []
    for (root, path, _), e in zip(work, entries):
        # Only where the translation changed the pitch. A machine whose translation plays as the original but whose
        # A3 measured off 220 Hz gets no "fix": on the unit solar and metal played right as they were and wrong
        # "fixed" (the measure took a harmonic, or an inharmonic sound's loudest partial, for the pitch).
        if e["status"] == "ok" and any("TRANSLATION CHANGES" in n for n in e["notes"]):
            fixes.append((root, path, "match", e["name"]))

    def fix_one(item):
        root, path, how, name = item
        rel = os.path.relpath(path, root)
        slug = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(root) + "_" + rel) + "-fix"
        fixed_name = field_name(name + "-fix" if len(name) <= 11 else name[:13] + "-f")
        r = subprocess.run([sys.executable, os.path.join(HERE, "x2f.py"), "build", path, "--name", fixed_name, "--out",
                            os.path.join(out, "build", slug)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           env=dict(os.environ, X2F_TUNE=how))
        lines = [l[5:].strip() for l in (r.stdout + r.stderr).splitlines() if l.startswith("x2f:")]
        if r.returncode:
            return None
        built = [os.path.join(out, "build", slug, f) for f in os.listdir(os.path.join(out, "build", slug)) if f.endswith(".pack")]
        meta = json.load(open(os.path.join(out, "build", slug, "x2f.json")))
        return {"source": os.path.join(os.path.basename(root), rel), "kind": "synth", "status": "ok", "built": built[0],
                "name": meta["name"], "screen": bool(meta.get("screen")), "drum": bool(meta.get("drum")),
                "colours": "switch" if meta.get("screen") else None, "variant": how,
                "pitch": next((l for l in lines if l.startswith("tuned")), None), "tuning": None, "knobs": None,
                "notes": ["pitch %s: %s" % ("matched to the original" if how == "match" else "set so A3 = 220 Hz", name)]}
    with ThreadPoolExecutor(max(1, jobs)) as pool:
        entries += [e for e in pool.map(fix_one, fixes) if e]

    names = {}
    for e in sorted((e for e in entries if e["status"] == "ok"), key=lambda e: (e["source"], e.get("colours") or "")):
        n = e["name"].replace(" ", "_")
        key = n
        names[key] = names.get(key, 0) + 1
        first = names[key] == 1
        file = "x2f-%s%s.pack" % (n, "" if first else "-%d" % names[key])
        shutil.copyfile(e.pop("built"), os.path.join(building, file))
        info = read_pack(os.path.join(building, file))
        e.update(file=file, room=info["room"], size=os.path.getsize(os.path.join(building, file)))
        if not first:
            e["notes"].append("same name as another machine's pack (%s): the Field registers only the first" % n)
    shutil.rmtree(packs_dir, ignore_errors=True)
    os.rename(building, packs_dir)
    doc = {"made": time.strftime("%Y-%m-%d %H:%M"), "packs_folder": packs_dir, "limits": {
        "packs": PACKS_MOST, "engines": ENGINES_MOST, "effects": EFFECTS_MOST, "room": ROOM_BYTES},
        "entries": sorted(entries, key=lambda e: (e["status"] != "ok", e.get("kind") or "", e["source"]))}
    with open(os.path.join(out, "library.json"), "w") as f:
        json.dump(doc, f, indent=1)
    ok = [e for e in entries if e["status"] == "ok"]
    print("x2f library: %d packs (%d synths, %d effects) in %s; %d not translated (players); "
          "%d with no sound source; %d failed; library.json beside them"
          % (len(ok), sum(e["kind"] == "synth" for e in ok), sum(e["kind"] == "effect" for e in ok), packs_dir,
             sum(e["status"] == "not translated" for e in entries), sum(e["status"] == "no sound source" for e in entries),
             sum(e["status"] == "failed" for e in entries)))
    for e in entries:
        if e["status"] == "not supported":
            print("  not supported on open-field: %s" % e["source"])
    for e in entries:
        if e["status"] == "failed":
            print("  failed: %s: %s" % (e["source"], e["reason"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    point = argparse.ArgumentParser(add_help=False)
    for key, (flag, what, _) in SETTINGS.items():
        point.add_argument(flag, dest=key, help=what)
    sub = ap.add_subparsers(dest="cmd", required=True)
    su = sub.add_parser("setup", parents=[point], help="point x2f at what it needs (asks for anything not given) and remember")
    su.add_argument("--colours", "--colors", dest="colours", choices=("partial", "full", "own", "p-flip", "f-flip"),
                    help="the default screen colours for every machine not set otherwise")
    sub.add_parser("where", help="show what x2f remembers and whether each still holds")
    r = sub.add_parser("reset", help="forget remembered places (all, or the ones named)")
    r.add_argument("keys", nargs="*", metavar="KEY", help="any of: " + ", ".join(SETTINGS))
    g = sub.add_parser("clone", help="git clone a missing requirement (open-xy: also fetches its toolchain)")
    g.add_argument("what", choices=list(REPOS))
    g.add_argument("--into", help="parent folder to clone into (asked if not given)")
    g.add_argument("--from", dest="url", help="the repository (default: the one last used, else %s's home today)" % "each")
    for c in ("translate", "build"):
        s = sub.add_parser(c, parents=[point])
        s.add_argument("machine", help="an Open-XY machine folder (has machine.json and definition.json)")
        s.add_argument("--out", help="output folder (default out/<name>)")
        s.add_argument("--name", help="the name in the unit's lists (default: the machine's id)")
        s.add_argument("--cycles", type=int, help="declared cost a call (synth: per voice, <= 40000)")
        s.add_argument("--rides", choices=STOCK_EFFECTS, help="effect: the stock effect it stands on")
        s.add_argument("--colours", "--colors", dest="colours", choices=("partial", "full", "own", "p-flip", "f-flip"),
                       help="screen colours: partial (a part one knob moves takes its colour; the default), full "
                            "(everything a knob animates, white too), own (as the machine drew it). Remembered")
        s.add_argument("--sample-mb", type=float, help="sampler: working memory for its sample, in MB (default 2: "
                       "two halves of 1 MB, about 11 s of mono 44.1 kHz 16-bit)")
        s.add_argument("--knobs", help="the machine controls (ids, comma separated) the Field's knobs turn: up to 8 "
                                       "for a synth, 4 for an effect. Remembered for that machine")
        s.add_argument("--set", action="append", metavar="ID=VALUE", help="fix a control not on a knob (or a knob's "
                                       "start): 0..1, a percent, or a choice's label. Repeatable; remembered")
        s.add_argument("--include", action="append", metavar="DIR", help="a folder of headers the machine needs "
                                       "that it does not ship (a private table, say). Repeatable; remembered")
        s.add_argument("--tune", help="synth: keep (as it plays), match (as the original plays), fix (A3 = 220 Hz), or "
                                      "what A3 should sound as: a note (C4, Bb2) or Hz (261.63). Remembered for that machine")
    ft = sub.add_parser("fit", help="will these packs run together? kinds, room, the 32/8/16 limits")
    ft.add_argument("packs", nargs="+", help=".pack files or folders of them (include base.pack)")
    ft.add_argument("--json", action="store_true", help="the answer as JSON, for other tools")
    lb = sub.add_parser("library", help="build every machine under the folders given into one library of packs, "
                                        "with library.json saying what each is (for choosing a set)")
    lb.add_argument("roots", nargs="+", help="folders to search for Open-XY machines")
    lb.add_argument("--skip", action="append", default=[], metavar="NAME", help="leave out folders of this name (repeatable)")
    lb.add_argument("--out", default="out/library", help="the library folder (default out/library)")
    lb.add_argument("--jobs", type=int, default=os.cpu_count() or 4)

    sub.add_parser("check", parents=[point], help="karplus + ringmod: translate, render on this computer, build packs")
    a = ap.parse_args(argv)
    flags = {k: getattr(a, k, None) for k in SETTINGS}
    if a.cmd == "setup":
        if a.colours:
            saved = load_config(); saved["colours_default"] = a.colours; save_config(saved)
            print("x2f: screen colours by default: %s" % a.colours)
        return setup(flags)
    if a.cmd == "where":
        return where()
    if a.cmd == "reset":
        unknown = [k for k in a.keys if k not in SETTINGS and k not in ("tuning", "knobs", "sets", "includes", "colours", "colours_default")]
        if unknown:
            raise SystemExit("x2f: no such setting %s (any of: %s)" % (", ".join(unknown), ", ".join(SETTINGS)))
        return reset(a.keys)
    if a.cmd == "fit":
        return fit(a.packs, a.json)
    if a.cmd == "library":
        return library(a.roots, a.skip, a.out, a.jobs, (None,))
    if a.cmd == "clone":
        return clone_command(a.what, a.into, a.url)
    configure(a.cmd, flags)
    if a.cmd == "check":
        return check()
    if getattr(a, "include", None):
        saved = load_config()
        saved.setdefault("includes", {})[os.path.abspath(a.machine)] = [absolute(d) for d in a.include]
        save_config(saved)
    meta = translate(a.machine, a.out, a.name, a.cycles, a.rides, knobs_arg=a.knobs, sets_arg=a.set, colours_arg=a.colours, sample_mb=a.sample_mb)
    if a.cmd == "build":
        build(meta, a.tune)
    return 0


if __name__ == "__main__":
    sys.exit(main())
