#!/usr/bin/env python3
"""
ds10_to_dsn12.py - converter of KORG DS-10 (Nintendo DS) save files into KORG DSN-12 (Nintendo 3DS) songs.

Usage:
  python3 ds10_to_dsn12.py "DS-10 input.sav" [-o folder] [--songs 1,3]

Each song of the .sav becomes a .sng file, and a catalog database_v1.dat is created next to them. The result goes into
`folder` (default: dsn12_out). Song numbers in the output and in --songs are the same as in the DS-10 interface (from 1).
The DSN-12 channel layout is fixed: SYN1 and SYN2 are channels 1 and 2, DRUM1..4 are channels 3..6 (the order of the parts on
the DS-10); channels 7..12 are empty.

What is converted:
  song name, tempo, swing, mixer (volume and pan of the 6 parts), the sequence of patterns (scenes), the number of steps of
  each pattern, notes/gate/volume/pan of all 6 parts (SYN1, SYN2, DRUM1..4),
  synthesizer parameters (31 parameters, index by index), tone names,
  KAOSS octave and assignments,
  the DS-10 effect (DELAY/CHORUS/FLANGER and its parameters) -> FX1 of the DSN-12, FX ASSIGN -> channel sends to FX1,
  the DS-10 MUTE timeline -> the scene mute masks of the DSN-12 (song position -> scene).
The DSN-12 checksums (field 0x1C of the .sng and 0x08 of database_v1.dat) are computed: CRC-32.
FX2 and REVERB of the DSN-12 stay as in the reference song (the DS-10 has none). The formats are described in KORG_DS_format.md.
"""
import argparse
import base64
import hashlib
import os
import struct
import sys
import time
import zlib

# ============================================================================
#  KORG DS-10 save file format (.sav, 256 KiB or a 512 KiB dump)
# ============================================================================
DS10_MAGIC = b"DS10PSAVEDATA000"
DS10_SLOT0 = 0x100            # first song slot
DS10_SLOT_STRIDE = 0x3000     # distance between slots
DS10_SLOT_SIZE = 0x2FEC       # size of the slot structure (the MD5 covers [0x10:0x2FEC])
DS10_NUM_SLOTS = 18
DS10_SEQ_OFFSET = 0x34        # u16[100] inside the slot, 0xFFFF = empty
DS10_SEQ_LEN = 100
DS10_PATTERNS_OFFSET = 0xFC   # 16 blocks of 0x2A4 bytes
DS10_PATTERN_SIZE = 0x2A4
DS10_NUM_PATTERNS = 16
DS10_TONELIB = 0x38000        # global tone library: 24 records of 0x100 bytes
DS10_TONELIB_RECORDS = 24

# Inside a pattern block (0x2A4 bytes):
P_STEPS = 0x000        # u32: number of steps (1..16)
P_SYN1 = 0x004         # 31 bytes of SYN1 parameters
P_SYN2 = 0x023         # 31 bytes of SYN2 parameters
P_SYN_STEPS = 0x042    # 2 x 16 x 4 bytes [gate code, note, volume, pan]
P_DRUM_STEPS = 0x0C2   # 4 x 16 x 4 bytes
P_KAOSS_STEPS = 0x1C2  # 2 x 16 x 4 bytes [X, Y, X assignment, Y assignment]  (ff ff 0b 0c = not set)
P_MISC = 0x242         # 26 bytes: effect (bytes 0..8), mixer (9..22); every pattern has its own
P_PARTS = 0x25C        # 6 x 12 bytes: part records (KAOSS octave, assignments, ...)
# Slot tail after the 16 pattern blocks (offsets relative to the start of the tail):
T_DRUMS = 0            # 4 x 31 bytes of DRUM1..4 parameters (shared by the whole song)
T_MISC = 124           # 36 bytes (not decoded)
T_NAMES = 160          # 8-byte tone names: index = pattern*6 + part (16*6 + 6 = 102 entries)
T_MUTE = 0x2F0C - (DS10_PATTERNS_OFFSET + DS10_NUM_PATTERNS * DS10_PATTERN_SIZE)   # MUTE timeline: right after the names
MUTE_WORDS = 20        # 20 u32 LE words = 100 positions; 5 positions of 6 bits per word (bit v = part v), bits 30..31 unused
MUTE_PER_WORD = 5

EMPTY_NOTE = 0xFF
DS10_INIT_PARAMS = bytes([0, 0, 0, 0, 0, 0, 1, 1, 3, 0, 0, 127, 0, 0, 0, 0, 0, 0, 0, 105, 0, 66, 127, 0, 19, 0, 0, 0, 0, 0, 0])


class DS10Pattern:
    __slots__ = ("steps", "syn_params", "syn_steps", "drum_steps", "kaoss_steps", "misc", "parts")


class DS10Song:
    __slots__ = ("slot", "name", "tempo", "swing", "field_1c", "seq", "patterns",
                 "drum_params", "trailer_misc", "tone_names", "mute", "md5_ok")


def parse_ds10(data):
    if data[:16] != DS10_MAGIC:
        raise ValueError("This is not a KORG DS-10 save file (signature DS10PSAVEDATA000 not found)")
    if len(data) < DS10_TONELIB + DS10_TONELIB_RECORDS * 0x100:   # 256 KiB (clean) or 512 KiB (cartridge dump)
        raise ValueError("The file is too short: %d bytes" % len(data))
    if hashlib.md5(data[:0x1C]).digest() != data[0x1C:0x2C]:
        print("  WARNING: the MD5 of the file header does not match", file=sys.stderr)
    songs = []
    for i in range(DS10_NUM_SLOTS):
        base = DS10_SLOT0 + i * DS10_SLOT_STRIDE
        slot = data[base:base + DS10_SLOT_SIZE]
        if not any(slot[:16]):           # zero hash -> empty slot
            continue
        s = DS10Song()
        s.slot = i
        s.md5_ok = hashlib.md5(slot[0x10:DS10_SLOT_SIZE]).digest() == slot[:16]
        s.tempo = struct.unpack_from("<H", slot, 0x14)[0]
        s.swing = struct.unpack_from("<H", slot, 0x1A)[0]
        s.field_1c = struct.unpack_from("<I", slot, 0x1C)[0]
        s.name = slot[0x24:0x2C].split(b"\0")[0].decode("ascii", "replace")
        seq = struct.unpack_from("<100H", slot, DS10_SEQ_OFFSET)
        s.seq = [v if v != 0xFFFF else None for v in seq]
        s.patterns = []
        for p in range(DS10_NUM_PATTERNS):
            b = slot[DS10_PATTERNS_OFFSET + p * DS10_PATTERN_SIZE:
                     DS10_PATTERNS_OFFSET + (p + 1) * DS10_PATTERN_SIZE]
            pat = DS10Pattern()
            pat.steps = struct.unpack_from("<I", b, P_STEPS)[0]
            pat.syn_params = [b[P_SYN1:P_SYN1 + 31], b[P_SYN2:P_SYN2 + 31]]
            pat.syn_steps = [[b[P_SYN_STEPS + (t * 16 + k) * 4:P_SYN_STEPS + (t * 16 + k) * 4 + 4]
                              for k in range(16)] for t in range(2)]
            pat.drum_steps = [[b[P_DRUM_STEPS + (t * 16 + k) * 4:P_DRUM_STEPS + (t * 16 + k) * 4 + 4]
                               for k in range(16)] for t in range(4)]
            pat.kaoss_steps = [[b[P_KAOSS_STEPS + (t * 16 + k) * 4:P_KAOSS_STEPS + (t * 16 + k) * 4 + 4]
                                for k in range(16)] for t in range(2)]
            pat.misc = b[P_MISC:P_MISC + 26]
            pat.parts = [b[P_PARTS + t * 12:P_PARTS + (t + 1) * 12] for t in range(6)]
            s.patterns.append(pat)
        tr = DS10_PATTERNS_OFFSET + DS10_NUM_PATTERNS * DS10_PATTERN_SIZE
        s.drum_params = [slot[tr + T_DRUMS + k * 31:tr + T_DRUMS + (k + 1) * 31] for k in range(4)]
        s.trailer_misc = slot[tr + T_MISC:tr + T_MISC + 36]
        # MUTE timeline: mute[i] = 6-bit mask of song position i (from 0), bit v = part v is muted (KORG_DS_format.md, 1.7)
        words = struct.unpack_from("<%dI" % MUTE_WORDS, slot, tr + T_MUTE)
        s.mute = [(words[i // MUTE_PER_WORD] >> (6 * (i % MUTE_PER_WORD))) & 0x3F for i in range(DS10_SEQ_LEN)]
        s.tone_names = []
        for p in range(DS10_NUM_PATTERNS):
            row = []
            for part in range(6):
                o = tr + T_NAMES + (p * 6 + part) * 8
                row.append(slot[o:o + 8].split(b"\0")[0].decode("ascii", "replace"))
            s.tone_names.append(row)
        songs.append(s)
    return songs


VOLUME_LEVELS = [i * 127 // 11 for i in range(12)]   # 12 note volume levels: 0,11,23,34,46,57,69,80,92,103,115,127


# The same positions in the DSN-12: L2=0, L1=32, C=64, R1=95, R2=127 (left = 0, as in the mixer)
PAN_MIRROR = {127: 0, 96: 32, 64: 64, 32: 95, 0: 127}

# DSN-12 gate/legato: the code is one less than in the DS-10: 0=25%, 1=50%, 2=75%, 3=100%, 4=legato (2 = default)
def gate_to_dsn12(code):
    return code - 1 if 1 <= code <= 5 else 2

# Volume and KAOSS X/Y are stored as a number 0..127 in both programs; the DS-10 editor grid has 12 levels,
# the DSN-12 grid has 16 (0, 8, 17, 25, 34, 42, 51, 59, 68, 76, 85, 93, 102, 110, 119, 127).
LEVELS16 = [round(i * 127 / 15) for i in range(16)]


def rescale_12_to_16(b):
    """DS-10 value (12 grid levels) -> the nearest DSN-12 level by position (16 levels).
    Values outside the grid (KAOSS recorded from the pad) are scaled proportionally from 0..127 to 16 levels."""
    if b in VOLUME_LEVELS:
        idx = int(VOLUME_LEVELS.index(b) * 15 / 11 + 0.5)
    else:
        idx = int(b * 15 / 127 + 0.5)
    return LEVELS16[idx]


# ============================================================================
#  KORG DSN-12 song (.sng) and catalog (database_v1.dat) format
# ============================================================================
DSN12_MAGIC = b"ds20"
DSN12_VERSION = b"\x00\x00\x02\x01"
DSN12_NUM_PATTERNS = 64
DSN12_NUM_TRACKS = 12
DSN12_NUM_STEPS = 64
DSN12_NUM_SCENES = 99
DSN12_EMPTY_STEP = bytes.fromhex("ff027f40ffff7f00")
DSN12_PRMS_TAIL = (0, 0, 0, 64, 0, 0, 0, 0, 0)   # parameters 31..39 (DSN-12 default values)
PRM_RELEASE = 23       # EG: 20 = ATTACK, 21 = DECAY, 22 = SUSTAIN, 23 = RELEASE (KORG_DS_format.md, 1.8 and 2.6)
PRM_PORTA = 2          # 0 = OCTAVE, 1 = EG INT (pitch), 2 = PORTA
# DS-10 drums have no RELEASE and PORTA knobs, but the bytes hold hidden values (RELEASE ~ decay, PORTA from a copied tone),
# and the DSN-12 really applies them. So for drums: RELEASE = the middle of the scale, PORTA = 0.
DRUM_RELEASE = 64
DRUM_PORTA = 0
DSN12_DEFAULT_MIXR = (100, 64, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0)
DRUM_TRACK0 = 2        # DRUM1..4 are on channels 3..6 (indices 2..5), right after SYN1/SYN2, as the order of the parts on the DS-10
DS10_NUM_PARTS = 6     # SYN1, SYN2, DRUM1..4
MIXR_SEND_FX1 = 4      # mixr words: 4/5/6 = send to FX1/FX2/REV (0/1), 7 = mute (KORG_DS_format.md, 2.7)

# --- Effects (KORG_DS_format.md, 1.6 and 2.8) ---
DS10_FX_ASSIGN = ["SYN1", "SYN2", "SYN1+2", "DRUMS", "ALL", "OFF"]      # byte 0 of the DS-10 effect
# which DS-10 parts (0 = SYN1, 1 = SYN2, 2..5 = DRUM1..4) hear the effect for a given FX ASSIGN
DS10_FX_PARTS = [{0}, {1}, {0, 1}, {2, 3, 4, 5}, {0, 1, 2, 3, 4, 5}, set()]
DS10_FX_TYPES = 3                                                       # 0 = DELAY, 1 = CHORUS, 2 = FLANGER
DSN12_FX_SEL = 72          # efct = 80 s16 words = 10 rows of 8 (one row per effect type); word 72 = the selected row

# Template: the mixs, efcs, tons (128 tones) and oslo chunks of the reference DSN-12 song
# (zlib + base64).
TEMPLATE_B64 = """
eNrtm91uG0UUx8+6DWpRW0COSVJSmtAWmaotnvV+IqjtJlZtGpwQO1dIiH6hpmo+VEciCIlc8ho8Aq/Au3DJJVwhsZw5O95utna9
G1X6c9GNPLuz8c5vzpmZ304usrN9OOyUiHa2D59XiWj/+c5wns+PqMmlRdmjvb66pM9vvv/m++nvj86Pv384rJXkfPCbef5XebLB
ZZVmzXNVbq+ZameWa7NUTer6d08zrK3UdUPam1yfdlgT4vi/95tO2O9ZM4KUqldTd3RU32XaTtePR128PjNl3hzs7Q7P8CPDH3cP
Dk0cGy+NmUWn5Ooo85tt/tyRu+UxOUr3ZPf+zuN3+dztdQd8UvTXHK38Ukn6keX/GzXGzJojM3bxcT0hV3Lz73VX7qlc/PPJszr2
P6Kf5HqRP5dNL64I/7bkoDwm55P4di7+lRS/RH9GPyT1RSlv0BfCvynzrQi/XjD/pST/79Ealx+aWhz/kmRkrkD+nRz89KHzH0XN
MfNP1xfkk5ff77U222o6P4rOHeP/HZ3l8iL1UwarnSB+4dtF4m8w/xSX1/j6Ei3LaI+i/1mWkv6Ux6z3ifx6nvgbqfg1X8f60TGD
fyr3luV+uUj8znT+C8rvUcxvSostsRFJLprCn5fPfsoUpVfwV9ZaGyrX/Gtk8n+frwP6KuFf55Wv+e+L1fdT+Z/GtwvP/7T/rMwb
riLrMu/863Q7rQHlGX86tv5j/79N3He6Ze5/IP1xeSTmpBd5+Hda/b4qyLe4B0u8xiyxnl538REI3+b1EBTi2wX5cQ8GXB4Z98eH
Lfyzsu8oF+DXC8dv0T/iv0tS/9jsJC6aNdnk/F/L+L/xCr5TmB870KILsgoWzf3LzL/Ac/A2Z6dI/G5OfiWV/fio0C6Xc0yNV8M3
ekdBn9G51PhbU/ke5fXPaXkT6vzPyHv+Buec6Kppc4FH3uKmHJ6RWxn3TOKvtVurKvf6nxHP6FbPc2t6/r2TinKZ52TZ7HJHfZ7J
wbepiH9PM71k3jnfmvc+mfee3sp9yePRYy++6NdbU/j1QvyRf3oy4lcz+89PaJP3y+uZXa+e/9b4989gs9u726ci/rWS998Z2W2M
/LdCT7h8Jn95zOec/4POVm+1vZmTv5BkIHbNKn2dyowlY/85PeDyiVmj0/gbnfVeW98oFP/r2/8PGH+zZrtgvgfm+2B+8Hr4zZPy
Qyy/XgPzFZhvg/l1MN8B810w3wPzfTAf7L862H8O2H8O2H8O2H8O2H8O2H8O2H8O2H8O2H8O2H8O2H8u2H8u2H8u2H8u2H8u2H8u
2H8u2H8u2H8u2H8u2H8e2H8e2H8e2H8e2H8e2H8e2H8e2H8e2H8e2H8e2H8+2H8+2H8+2H8+2H8+2H8+2H8+2H8+2H8+2H8+2H8B
2H8B2H8B2H8B2H8B2H8B2H8B2H8B2H8B2H8B2H8h2H8h2H8h2H8h2H8h2H8h2H8h2H8h2H8h2H8h1n+qVgPzFZhvg/l1MN8B810w
3wPzfTA/APPB/lNg/ymw/xTYfwrsPwX2nwL7T4H9p8D+U2D/KbD/bLD/bLD/bLD/bLD/bLD/bLD/bLD/bLD/Xv7/j9F5b/hsr2b6
sWTYTfl5SutjGKPn/gMoeveP
"""


def chunk(tag, payload=b""):
    assert len(tag) == 4
    pad = (-len(payload)) % 4
    return tag + struct.pack("<I", len(payload)) + payload + b"\0" * pad


EOD = chunk(b"EOD ")


def dsn12_children(buf, off, end):
    """Iterates over the chunks [tag, u32 len, data, pad4] in a range."""
    res = []
    while off + 8 <= end:
        tag = buf[off:off + 4]
        ln = struct.unpack_from("<I", buf, off + 4)[0]
        if not all(32 <= c < 127 for c in tag) or off + 8 + ln > end:
            break
        res.append((tag, off + 8, ln))
        off += 8 + ((ln + 3) & ~3)
    return res


def dsn12_find(buf, off, end, tag):
    for t, o, l in dsn12_children(buf, off, end):
        if t == tag:
            return o, l
    raise KeyError(tag)


def load_template(blob):
    """Returns a dict tag -> raw chunk (with its header) for mixs/efcs/tons/oslo."""
    out = {}
    for tag, o, l in dsn12_children(blob, 0, len(blob)):
        out[tag] = blob[o - 8:o + l + ((-l) % 4)]
    return out


DSN12_NAME_TAIL = bytes.fromhex("0000170043871500")   # bytes 8..15 of a tone name, the same in all DSN-12 files


def name16(name, tail=b"\0" * 8):
    b = name.encode("ascii", "replace")[:8]
    return b + b"\0" * (8 - len(b)) + tail


DSN12_NDAT = bytes.fromhex("112b16110300db07")   # the same in all DSN-12 songs (2011-04-17 22:43:17)


def date8(t=None):
    tm = time.localtime(t)
    # [second, minute, hour, day, month(0..11), 0x9A, year u16]; byte 5 is always 0x9A
    return struct.pack("<6BH", tm.tm_sec, tm.tm_min, tm.tm_hour, tm.tm_mday, tm.tm_mon - 1, 0x9A, tm.tm_year)


def build_trkd(prms31, tone, steps16, part_rec, kaoss16, drum=False):
    """One track of a DSN-12 pattern built from the data of a DS-10 part."""
    # KAOSS / display
    koct = part_rec[1] if part_rec[1] else 3
    kosa = bytes([part_rec[2], 0, part_rec[3], 0]) if part_rec[2] else bytes([0x0B, 0, 0x0C, 0])
    dpos = bytes([0x48, 0, 0, 0, 0, 0, 0, 0])   # editor scroll position of the DSN-12 (default value)
    body = chunk(b"koct", bytes([koct])) + chunk(b"kost", b"\0") + chunk(b"kosm", b"\x01") + \
        chunk(b"koss", bytes([0, 1, 2, 0])) + chunk(b"kosa", kosa) + chunk(b"dpos", dpos)
    # DS-10 step:  [gate/legato, note, volume (12 levels, 0..127), pan (l2..r2 = 127..0)] + KAOSS X/Y
    # DSN-12 step: [note, gate/legato, volume, pan, KAOSS X, KAOSS Y, 7f, 00]
    st = bytearray()
    for k in range(DSN12_NUM_STEPS):
        gate, n, vol, pan = steps16[k] if k < len(steps16) else (0, EMPTY_NOTE, 0x7F, 0x40)
        # A drum hit is a step with a note (as for synthesizers). An erased hit keeps bit 2 of the first byte (value 6):
        # `06 ff` is an erased hit, not a hit (KORG_DS_format.md, 1.4).
        if n != EMPTY_NOTE:
            pan = PAN_MIRROR.get(pan, 127 - pan)   # NOTE pan: on the DS-10 left = 127, on the DSN-12 left = 0 (the mixer pan is not mirrored)
            x, y = (kaoss16[k][0], kaoss16[k][1]) if kaoss16 else (0xFF, 0xFF)
            vol = rescale_12_to_16(vol)            # 12 levels of the DS-10 grid -> 16 levels of the DSN-12
            x, y = (x if x == 0xFF else rescale_12_to_16(x)), (y if y == 0xFF else rescale_12_to_16(y))
            st += bytes([n, gate_to_dsn12(gate), vol, pan, x, y, 0x7F, 0x00])
        else:
            st += DSN12_EMPTY_STEP
    body += chunk(b"step", bytes(st))
    vals = list(struct.unpack("<31b", prms31)) + list(DSN12_PRMS_TAIL)
    if drum:
        vals[PRM_RELEASE] = DRUM_RELEASE
        vals[PRM_PORTA] = DRUM_PORTA
    prms = struct.pack("<40h", *vals)
    body += chunk(b"synt", chunk(b"prms", prms) + chunk(b"name", name16(tone, DSN12_NAME_TAIL)) + EOD)
    return chunk(b"trkd", body + EOD)


def build_patn(song, pi):
    p = song.patterns[pi] if pi < DS10_NUM_PATTERNS else None
    pname = "PTN-%02d" % pi
    body = chunk(b"name", name16(pname))
    body += chunk(b"tmpo", b"\xff\xff")
    body += chunk(b"lstp", bytes([p.steps]) if p and p.steps != 16 else b"\xff")
    body += chunk(b"name", name16(pname))
    trks = b""
    for t in range(DSN12_NUM_TRACKS):
        d = t - DRUM_TRACK0
        default_name = "TONE%02d%02d" % (pi + 1, t + 1)      # default name in the DSN-12: pattern + track
        if p is not None and t < 2:
            tone = song.tone_names[pi][t] or default_name
            trks += build_trkd(p.syn_params[t], tone, p.syn_steps[t][:p.steps], p.parts[t], p.kaoss_steps[t])
        elif 0 <= d < 4:
            # drum parameters are shared by the song: they are also written into the unused patterns 16..63
            trks += build_trkd(song.drum_params[d], default_name, p.drum_steps[d][:p.steps] if p else [],
                               p.parts[2 + d] if p else bytes(12), None, drum=True)
        else:
            trks += build_trkd(DS10_INIT_PARAMS, default_name, [], bytes(12), None)
    body += chunk(b"trks", trks + EOD)
    return chunk(b"patn", body + EOD)


def ds10_first_pattern(song):
    """The DS-10 stores the mixer and the effect in every pattern (block 0x242), the DSN-12 keeps them once per song:
    the pattern of the first scene is used (or pattern 0 if the sequence is empty)."""
    seq = [v for v in song.seq if v is not None]
    return seq[0] if seq else 0


def ds10_effect(song):
    """The DS-10 effect (bytes 0..8 of block 0x242 of the pattern of the first scene, KORG_DS_format.md, 1.6).
    The values are brought to the DSN-12 ranges: u7 = 0..127, s7 = -63..+63 (L/R and FEEDBACK are stored as a signed byte).
    'differs' = the effect differs between the patterns used by the song."""
    pi = ds10_first_pattern(song)
    m = song.patterns[pi].misc

    def u7(v):
        return min(v, 127)

    def s7(v):
        return max(-63, min(63, v - 256 if v > 127 else v))

    used = {v for v in song.seq if v is not None}
    return {"pattern": pi, "assign": m[0], "type": m[1], "delay_time": u7(m[2]), "lr": s7(m[3]),
            "lfo": u7(m[4]), "depth": u7(m[5]), "sync": 1 if m[6] else 0, "feedback": s7(m[7]), "drywet": u7(m[8]),
            "differs": any(song.patterns[q].misc[0:9] != m[0:9] for q in used)}


def fx1_words(fx, words):
    """A copy of the efct words with the DELAY/CHORUS/FLANGER rows and the selected type taken from the DS-10 effect.
    The DS-10 stores the parameters of all three effects at once (FEEDBACK and DRY/WET are shared), so all three rows are filled;
    the type codes DELAY/CHORUS/FLANGER are the same in both programs (0/1/2)."""
    w = list(words)
    w[0:5] = [fx["sync"], fx["delay_time"], fx["lr"], fx["feedback"], fx["drywet"]]     # row 0: DELAY
    w[8:11] = [fx["lfo"], fx["depth"], fx["drywet"]]                                    # row 1: CHORUS
    w[16:20] = [fx["lfo"], fx["depth"], fx["feedback"], fx["drywet"]]                   # row 2: FLANGER
    w[DSN12_FX_SEL] = fx["type"]
    return w


def parse_efcs(chunk_bytes):
    """Raw efcs chunk (with its header) -> [3 lists of 80 s16 words]."""
    blocks = []
    for tag, o, l in dsn12_children(chunk_bytes, 8, len(chunk_bytes)):
        if tag == b"efct":
            po, pl = dsn12_find(chunk_bytes, o, o + l, b"prms")
            blocks.append(list(struct.unpack_from("<%dh" % (pl // 2), chunk_bytes, po)))
    return blocks


def build_efcs_chunk(blocks):
    body = b"".join(chunk(b"efct", chunk(b"prms", struct.pack("<%dh" % len(w), *w)) + EOD) for w in blocks)
    return chunk(b"efcs", body + EOD)


def ds10_mixer(song):
    """The DS-10 mixer (block 0x242 of the pattern of the first scene, see ds10_first_pattern).
    Returns ([volume x6], [DSN-12 pan x6], pattern number)."""
    pi = ds10_first_pattern(song)
    misc = song.patterns[pi].misc
    vol = [misc[17 + k] for k in range(6)]            # 0..127, as in the DSN-12
    pan = [misc[9 + k] for k in range(6)]             # as in the DSN-12: 0 = left, 127 = right; copied as is
    return vol, pan, pi


def dsn12_part(t):
    """The DS-10 part (0 = SYN1, 1 = SYN2, 2..5 = DRUM1..4) that plays on the DSN-12 channel t (from 0), or None.
    The layout is fixed: channels 1..6 = the DS-10 parts in the same order (SYN1, SYN2, DRUM1..4), channels 7..12 are empty."""
    return t if t < DS10_NUM_PARTS else None


def build_mixs(song, fx):
    vol, pan, pi = ds10_mixer(song)
    used = {v for v in song.seq if v is not None}
    if any(song.patterns[q].misc[9:23] != song.patterns[pi].misc[9:23] for q in used):
        print("  WARNING: in '%s' the DS-10 mixer differs between patterns; the DSN-12 keeps one mixer, "
              "the mixer of pattern %d was used" % (song.name, pi + 1), file=sys.stderr)
    heard = DS10_FX_PARTS[fx["assign"]] if fx["assign"] < len(DS10_FX_PARTS) else set()   # FX ASSIGN -> sends to FX1
    body = b""
    for t in range(DSN12_NUM_TRACKS):
        prm = list(DSN12_DEFAULT_MIXR)
        part = dsn12_part(t)
        if part is not None:
            prm[0], prm[1] = vol[part], pan[part]
        prm[MIXR_SEND_FX1] = 1 if part in heard else 0      # by default no channel is sent to any effect in the DSN-12
        body += chunk(b"mixr", chunk(b"prms", struct.pack("<12H", *prm)) + EOD)
    return chunk(b"mixs", body + EOD)


def dsn12_scene_mute(mute6):
    """The DSN-12 scene mute mask (scne/mute, u16) from the 6-bit part mask of the DS-10.
    Bit t = channel (Tr) t+1 is muted, 0 = plays (KORG_DS_format.md, 2.9).
    The parts go to the same channels as in the mixer (dsn12_part)."""
    m = 0
    for t in range(DSN12_NUM_TRACKS):
        part = dsn12_part(t)
        if part is not None and mute6 >> part & 1:
            m |= 1 << t
    return m


def build_efcs(song, template, fx):
    """efcs: the DS-10 effect goes to FX1; FX2 and REVERB stay as in the reference song."""
    if fx["type"] >= DS10_FX_TYPES:
        print("  WARNING: in '%s' unknown DS-10 effect type (%d), DELAY was used" % (song.name, fx["type"]), file=sys.stderr)
        fx = dict(fx, type=0)
    if fx["assign"] >= len(DS10_FX_ASSIGN):
        print("  WARNING: in '%s' unknown FX ASSIGN value (%d), the effect is not routed to any channel"
              % (song.name, fx["assign"]), file=sys.stderr)
    if fx["differs"]:
        print("  WARNING: in '%s' the DS-10 effect differs between patterns; the DSN-12 keeps one set of effects, "
              "the effect of pattern %d was used" % (song.name, fx["pattern"] + 1), file=sys.stderr)
    blocks = parse_efcs(template[b"efcs"])
    blocks[0] = fx1_words(fx, blocks[0])
    return build_efcs_chunk(blocks)


def build_dsn12_song(song, template):
    # sset - song settings
    sset = chunk(b"name", name16(song.name)) + chunk(b"ndat", DSN12_NDAT) + chunk(b"sdat", date8()) + \
        chunk(b"sloc", b"\0") + chunk(b"tmpo", struct.pack("<H", song.tempo)) + \
        chunk(b"lstp", bytes([16])) + chunk(b"swin", bytes([min(song.swing, 255)])) + \
        chunk(b"cpyi", struct.pack("<I", 1)) + EOD
    # pats - 64 patterns
    pats = b"".join(build_patn(song, pi) for pi in range(DSN12_NUM_PATTERNS)) + EOD
    # scns - 99 scenes (the song sequence)
    seq = song.seq[:DSN12_NUM_SCENES]
    if all(v is None for v in song.seq):
        seq = [0]     # the DS-10 song has no sequence: as in a new DSN-12 song, scene 0 = pattern 0
    if any(v is not None for v in song.seq[DSN12_NUM_SCENES:]):
        print("  WARNING: in '%s' the 100th song position is used, the DSN-12 has only 99 scenes; it was dropped"
              % song.name, file=sys.stderr)
    scns = b""
    for i in range(DSN12_NUM_SCENES):
        v = seq[i] if i < len(seq) else None
        mask = dsn12_scene_mute(song.mute[i])     # does not depend on the patn of the scene
        scns += chunk(b"scne", chunk(b"numb", bytes([i])) + chunk(b"patn", bytes([v if v is not None else 0xFF])) +
                      chunk(b"mute", struct.pack("<H", mask)) + EOD)
    scns += EOD
    fx = ds10_effect(song)
    body = chunk(b"sset", sset) + chunk(b"pats", pats) + build_mixs(song, fx) + \
        build_efcs(song, template, fx) + template[b"tons"] + chunk(b"scns", scns) + template[b"oslo"] + EOD
    return chunk(b"song", body) + EOD


# ---------- LZ11 (Nintendo) ----------
def lz11_compress(data, max_disp=0x1000):
    n = len(data)
    out = bytearray([0x11, n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF])
    table = {}
    pos = 0

    def add(p):
        if p + 3 <= n:
            table.setdefault(data[p:p + 3], []).append(p)

    while pos < n:
        flags = 0
        buf = bytearray()
        for bit in range(8):
            if pos >= n:
                break
            best_len = 0
            best_disp = 0
            if pos + 3 <= n:
                maxl = min(0x10110, n - pos)
                for start in table.get(data[pos:pos + 3], ()):   # the earliest occurrence wins when the length is equal
                    disp = pos - start
                    if disp > max_disp:
                        continue
                    l = 3
                    while l < maxl and data[start + l] == data[pos + l]:
                        l += 1
                    if l > best_len:
                        best_len, best_disp = l, disp
                        if l >= maxl:
                            break
            if best_len >= 3:
                flags |= 1 << (7 - bit)
                d = best_disp - 1
                l = best_len
                if l <= 0x10:
                    buf += bytes([((l - 1) << 4) | (d >> 8), d & 0xFF])
                elif l <= 0x110:
                    m = l - 0x11
                    buf += bytes([(m >> 4) & 0x0F, ((m & 0xF) << 4) | (d >> 8), d & 0xFF])
                else:
                    m = l - 0x111
                    buf += bytes([0x10 | ((m >> 12) & 0xF), (m >> 4) & 0xFF, ((m & 0xF) << 4) | (d >> 8), d & 0xFF])
                for i in range(l):
                    add(pos + i)
                pos += l
            else:
                buf.append(data[pos])
                add(pos)
                pos += 1
        out.append(flags)
        out += buf
    return bytes(out)


# ---------- DSN-12 checksums ----------
# CRC-32, polynomial 0x04C11DB7, most significant bit first (no reflection), final XOR 0xFFFFFFFF.
# The initial value depends on the file type:
#   .sng             : 0x64737864, data = everything from offset 0x20 to the end of the file (the LZ11 stream)
#   database_v1.dat  : 0x6473666D (= "mfsd" as a little-endian u32), data = from offset 0x10 to the end of the file
DSN12_SONG_CRC_INIT = 0x64737864
DSN12_DB_CRC_INIT = 0x6473666D


def _crc_table():
    t = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
        t.append(c)
    return t


_CRC_T = _crc_table()


def dsn12_crc(data, init):
    c = init
    for v in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _CRC_T[((c >> 24) ^ v) & 0xFF]
    return c ^ 0xFFFFFFFF


def dsn12_wrap(dec):
    comp = lz11_compress(dec)
    checksum = dsn12_crc(comp, DSN12_SONG_CRC_INIT)
    hdr = DSN12_MAGIC + DSN12_VERSION + struct.pack("<II", 0x20 + len(dec), 0x20 + len(comp)) + \
        b"\0" * 12 + struct.pack("<I", checksum)
    return hdr + comp


def build_database(names):
    text = "".join('{ "%s", "%s.sng" },\n' % (n, n) for n in names).encode("ascii") + b"\0"
    checksum = dsn12_crc(text, DSN12_DB_CRC_INIT)
    return b"mfsd" + struct.pack("<III", 16 + len(text), checksum, 0) + text


# ============================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("save", metavar="DS-10 input.sav", help="KORG DS-10 save file (.sav)")
    ap.add_argument("-o", "--out", metavar="folder", default="dsn12_out", help="folder for the result (default: dsn12_out)")
    ap.add_argument("--songs", help="song numbers separated by commas, as in the DS-10 interface (default: all non-empty songs)")
    args = ap.parse_args()

    try:
        songs = parse_ds10(open(args.save, "rb").read())
    except (OSError, ValueError) as e:
        ap.error(str(e))
    if args.songs:
        want = {int(x) - 1 for x in args.songs.split(",")}
        songs = [s for s in songs if s.slot in want]
    template = load_template(zlib.decompress(base64.b64decode(TEMPLATE_B64)))
    os.makedirs(args.out, exist_ok=True)
    names = []
    for s in songs:
        if not s.md5_ok:
            print("  WARNING: the MD5 of song %d does not match, the data may be damaged" % (s.slot + 1), file=sys.stderr)
        name = s.name or ("SONG%02d" % (s.slot + 1))
        base = name
        k = 2
        while name in names:
            name = "%s%d" % (base[:7], k)
            k += 1
        dec = build_dsn12_song(s, template)
        raw = dsn12_wrap(dec)
        path = os.path.join(args.out, name + ".sng")
        open(path, "wb").write(raw)
        names.append(name)
        print("song %2d '%s' -> %s (%d bytes, unpacked %d)" % (s.slot + 1, s.name, path, len(raw), len(dec)))
    if names:
        path = os.path.join(args.out, "database_v1.dat")
        open(path, "wb").write(build_database(names))
        print("catalog -> %s (%d song%s)" % (path, len(names), "" if len(names) == 1 else "s"))


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # quiet exit on `| head`
    except (ImportError, AttributeError):
        pass
    main()
