# KORG DS-10 and KORG DSN-12 file formats

This document describes the save file of KORG DS-10 (Nintendo DS, `.sav`) and the song files of KORG DSN-12
(Nintendo 3DS, `.sng` and `database_v1.dat`).

**Conventions.** All multi-byte numbers are little-endian. Offsets are hexadecimal and counted from 0. The applications
number patterns, steps, channels and songs from 1; where this document says "pattern 1" it means the first one, while array
indices and offsets start at 0. Types: `u8`/`s8` — unsigned/signed byte, `u16`/`s16`/`u32` — 16/32-bit integers,
`u7` — a value 0..127, `s7` — a value −63..+63 (stored as a signed byte or word).

---

# Part 1 — KORG DS-10 save file

## 1.1 File layout

The file is 256 KiB (`0x40000`); a cartridge dump may be 512 KiB with the second half filled with `0xFF`.

| Offset | Size | Content |
|---|---|---|
| `0x00` | 16 | signature `DS10PSAVEDATA000` |
| `0x10` | 4 | `00 00 00 00` in a file without songs, `00 FF FF FF` in a file with songs; meaning unknown |
| `0x14` | 8 | zeros |
| `0x1C` | 16 | MD5 of bytes `0x00..0x1B` |
| `0x2C` | | `0xFF` up to `0x100` |
| `0x100 + n·0x3000` | `0x2FEC` | song slot `n`; the area between `0x100` and the tone library holds 18 slots (n = 0..17); an empty slot is all zeros |
| `0x38000` | 24 × `0x100` | tone library (see 1.9); the content ends at `0x39800` |

Unused space is zero or `0xFF`.

## 1.2 Song slot (`0x2FEC` bytes)

Offsets are relative to the start of the slot.

| Offset | Size | Content |
|---|---|---|
| `0x00` | 16 | MD5 of bytes `0x10..0x2FEB` of the slot |
| `0x10` | 4 | version `02 00 01 00` |
| `0x14` | u16 | tempo, BPM |
| `0x16` | u16 | 0 |
| `0x18` | u16 | 12 (constant) |
| `0x1A` | u16 | swing, 50 = neutral |
| `0x1C` | u32 | unknown, normally 0 |
| `0x20` | 4 | zeros |
| `0x24` | 8 | song name, ASCII, terminated by `00` (bytes after the terminator are not significant) |
| `0x2C` | 8 | zeros |
| `0x34` | u16[100] | song sequence: pattern number 0..15 for each song position, `0xFFFF` = empty position |
| `0xFC` | 16 × `0x2A4` | pattern blocks (see 1.3) |
| `0x2B3C` | 4 × 31 | parameters of DRUM1..DRUM4 (shared by all patterns of the song, see 1.8) |
| `0x2BB8` | 36 | unknown |
| `0x2BDC` | 102 × 8 | tone names (see below) |
| `0x2F0C` | 20 × u32 | MUTE timeline (see 1.7) |
| `0x2F5C` | `0x8E` | unknown (zeros) |
| `0x2FEA` | 2 | `FF FF` |

**Song sequence.** Position `i` of the song plays the pattern stored at index `i`. The song ends at the last non-empty position.

**Tone names.** 8-byte ASCII strings terminated by `00`. Entry index = `pattern·6 + part` for the 16 patterns, where part 0 is SYN1,
part 1 is SYN2 and parts 2..5 (drums) are empty. Entries 96..101 form one more set of six names.

**Parts.** The six parts are always in this order: SYN1, SYN2, DRUM1, DRUM2, DRUM3, DRUM4.

## 1.3 Pattern block (`0x2A4` bytes)

Offsets are relative to the start of the block.

| Offset | Size | Content |
|---|---|---|
| `0x000` | u32 | number of steps, 1..16 |
| `0x004` | 31 | synthesizer parameters of SYN1 (see 1.8) |
| `0x023` | 31 | synthesizer parameters of SYN2 |
| `0x042` | 2 × 16 × 4 | steps of SYN1, then SYN2 (see 1.4) |
| `0x0C2` | 4 × 16 × 4 | steps of DRUM1..DRUM4 |
| `0x1C2` | 2 × 16 × 4 | KAOSS values of SYN1, then SYN2, per step: `[X, Y, X assignment, Y assignment]` |
| `0x242` | 26 | effect, mixer (see 1.5 and 1.6) |
| `0x25C` | 6 × 12 | part records |

**KAOSS values.** `X` and `Y` are 0..127; `0xFF` means "not set". The assignment bytes are `0x0B` and `0x0C` by default.
The editor grid has 12 levels, using the same values as note volume (1.4); values recorded from the pad can be any number 0..127.

**Part record** (12 bytes, one per part, in part order): byte 0 — editor view position (varies); byte 1 — KAOSS octave
(default 3, synthesizer parts only); bytes 2 and 3 — KAOSS X and Y assignment (`0x0B`, `0x0C`, synthesizer parts only);
the remaining bytes are constants of the part type and are not decoded.

## 1.4 Step encoding (4 bytes)

`[byte 0, note, volume, pan]`

| Field | Encoding |
|---|---|
| note | `octave·12 + semitone`; `0xFF` = no note. A step without a note is empty. The DS-10 interface labels note 0 as `C0` |
| byte 0 (synthesizer) | gate length: 1 = 25 %, 2 = 50 %, 3 = 75 %, 4 = 100 %, 5 = legato |
| byte 0 (drums) | usually 6 for a placed hit; its value in an empty step is not significant |
| volume | 12 levels: 0, 11, 23, 34, 46, 57, 69, 80, 92, 103, 115, 127 |
| pan | 5 positions: 127 = L2, 96 = L1, 64 = C, 32 = R1, 0 = R2 |

A drum hit is a step whose note is not `0xFF` (the default drum note is `0x3C`). A removed drum hit keeps byte 0 and has the note
`0xFF` (`06 FF 7F 40`).

## 1.5 Mixer (pattern block bytes `0x242 + 9 .. 0x242 + 22`)

The mixer is stored in every pattern block.

| Byte | Content |
|---|---|
| 9..14 | pan of the six channels (SYN1, SYN2, DRUM1..4): `u7`, 0 = left, 64 = center, 127 = right |
| 15..16 | unknown |
| 17..22 | volume of the six channels: `u7` |
| 23..25 | zeros |

## 1.6 Effect (pattern block bytes `0x242 + 0 .. 0x242 + 8`)

The effect is stored in every pattern block. The parameters of all three effect types are stored at the same time; only the
type byte selects the active one.

| Byte | Field | Values |
|---|---|---|
| 0 | FX ASSIGN | 0 = SYN 1, 1 = SYN 2, 2 = SYN 1+2, 3 = DRUMS, 4 = ALL, 5 = OFF |
| 1 | FX SELECT (type) | 0 = DELAY, 1 = CHORUS, 2 = FLANGER |
| 2 | DELAY TIME | `u7` (delay) |
| 3 | L/R RATIO | `s7`, −63 = L, 0 = center, +63 = R (delay) |
| 4 | LFO FREQ | `u7` (flanger, chorus) |
| 5 | DEPTH | `u7` (flanger, chorus) |
| 6 | SYNC | 0 = OFF, 1 = BPM (delay only) |
| 7 | FEEDBACK | `s7` (delay, flanger) |
| 8 | DRY/WET | `u7` (all types) |

## 1.7 MUTE timeline

The MUTE timeline is stored per song position (not per pattern), in 20 `u32` words starting at slot offset `0x2F0C`. Each word holds
5 positions of 6 bits; bits 30 and 31 are unused.

```
position i (from 0):   word = i div 5,   shift = 6 · (i mod 5)
mask = (word >> shift) & 0x3F
bit v of mask = 1      part v is muted at this position
                       (v: 0 = SYN1, 1 = SYN2, 2..5 = DRUM1..DRUM4)
```

## 1.8 Synthesizer parameters (31 bytes)

Every part — synthesizer or drum — has 31 parameter bytes (`s8`). Indices with a known meaning:

| Index | Parameter | Range |
|---|---|---|
| 0 | OCTAVE | signed |
| 1 | EG INT (pitch) | `s7` |
| 2 | PORTA | `u7` |
| 18 | DRIVE | `u7` |
| 19 | LEVEL (VCA) | `u7` |
| 20 | ATTACK | `u7` |
| 21 | DECAY | `u7` |
| 22 | SUSTAIN | `u7` |
| 23 | RELEASE | `u7` |

The other indices are not decoded. The DS-10 drum editor has no RELEASE and no PORTA control, but the two bytes (indices 23 and 2)
exist for drums as for any part. The default synthesizer patch (INIT) is:

```
00 00 00 00 00 00 01 01 03 00 00 7F 00 00 00 00 00 00 00 69 00 42 7F 00 13 00 00 00 00 00 00
```

## 1.9 Tone library (`0x38000`)

24 records of `0x100` bytes.

| Offset | Size | Content |
|---|---|---|
| `0x00` | 16 | MD5 of bytes `0x10..0x3E` of the record |
| `0x10` | 8 | tone name, ASCII, terminated by `00` |
| `0x18` | 8 | zeros |
| `0x20` | 31 | synthesizer parameters (1.8) |
| `0x3F` | | zeros up to `0x100` |

---

# Part 2 — KORG DSN-12 song file and catalog

## 2.1 Song file (`.sng`) container

| Offset | Size | Content |
|---|---|---|
| `0x00` | 4 | signature `ds20` |
| `0x04` | 4 | `00 00 02 01` (version) |
| `0x08` | u32 | `0x20` + length of the decompressed body |
| `0x0C` | u32 | file size |
| `0x10` | 12 | zeros |
| `0x1C` | u32 | CRC-32 of bytes `0x20..end of file` (see 2.11) |
| `0x20` | | body compressed with Nintendo LZ11 |

**LZ11.** The stream starts with the byte `0x11` followed by a 3-byte decompressed size (if that size is 0, a `u32` size follows).
Data is a sequence of groups: a flag byte, then 8 items (most significant bit first). A 0 bit is a literal byte. A 1 bit is a back
reference whose first byte `b1` selects the form (`ind = b1 >> 4`):

| `ind` | Size | Bytes | Copy length and distance |
|---|---|---|---|
| 0 | 3 bytes | `b1 b2 b3` | length = `((b1 & 15) << 4 \| b2 >> 4) + 0x11`; distance = `((b2 & 15) << 8 \| b3) + 1` |
| 1 | 4 bytes | `b1 b2 b3 b4` | length = `((b1 & 15) << 12 \| b2 << 4 \| b3 >> 4) + 0x111`; distance = `((b3 & 15) << 8 \| b4) + 1` |
| 2..15 | 2 bytes | `b1 b2` | length = `ind + 1`; distance = `((b1 & 15) << 8 \| b2) + 1` |

The distance counts back from the end of the output; the copy is byte by byte.

## 2.2 Chunk format and tree

The body is a tree of chunks: `[tag: 4 ASCII bytes][length: u32][data][padding with zeros to a multiple of 4]`. The length does not
include the padding. A container chunk holds child chunks and ends with an empty chunk `EOD ` (length 0). A song with 64 patterns
has a body of 597,928 bytes.

```
song
  sset                       song settings
  pats                       64 × patn
    patn
      name(16) tmpo(2) lstp(1) name(16)
      trks                   12 × trkd
        trkd
          koct(1) kost(1) kosm(1) koss(4) kosa(4) dpos(8)
          step(512)          64 steps × 8 bytes
          synt
            prms(80)         40 × s16
            name(16)
  mixs                       12 × mixr { prms(24) }
  efcs                       3 × efct { prms(160) }
  tons                       128 × synt { prms(80), name(16) }
  scns                       99 × scne { numb(1), patn(1), mute(2) }
  oslo                       prms(32)
EOD
```

Every container above (`song`, `pats`, `patn`, `trks`, `trkd`, `synt`, `mixs`, `mixr`, `efcs`, `efct`, `tons`, `scns`, `scne`, `oslo`,
`sset` and the top level) ends with `EOD `.

## 2.3 `sset` — song settings

| Chunk | Size | Content |
|---|---|---|
| `name` | 16 | song name: 8 ASCII bytes terminated by `00`; the remaining bytes are not significant |
| `ndat` | 8 | constant `11 2B 16 11 03 00 DB 07` |
| `sdat` | 8 | save time: `[second, minute, hour, day, month (0..11), 0x9A, year u16]` |
| `sloc` | 1 | song lock, 0 = unlocked |
| `tmpo` | 2 | tempo, BPM (`u16`) |
| `lstp` | 1 | last step of the song, 16 by default |
| `swin` | 1 | swing, 50 = neutral |
| `cpyi` | 4 | `01 00 00 00`, meaning unknown |

## 2.4 Patterns and tracks

`patn`: `name` (16) — pattern name, `PTN-00` for the first pattern (`PTN-01`, …); `tmpo` (2) — `FF FF` = use the song tempo;
`lstp` (1) — `FF` = use the song's last step, otherwise the number of steps; `name` repeats the pattern name; `trks` — 12 `trkd`.

A track (`trkd`) is a channel (Tr 01..Tr 12 in the interface, in file order).

| Chunk | Size | Content |
|---|---|---|
| `koct` | 1 | KAOSS octave, default 3 |
| `kost` | 1 | default 0, meaning unknown |
| `kosm` | 1 | default 1, meaning unknown |
| `koss` | 4 | default `00 01 02 00`, meaning unknown |
| `kosa` | 4 | KAOSS assignment of X and Y: `0B 00 0C 00` by default |
| `dpos` | 8 | editor scroll position; the first byte is `0x48` by default |
| `step` | 512 | 64 steps of 8 bytes (see 2.5) |
| `synt` | | the tone of the track: `prms` (80 bytes = 40 × `s16`, see 2.6) and `name` |

The tone name (`name`, 16 bytes) is 8 ASCII characters terminated by `00`, followed by the constant `00 00 17 00 43 87 15 00`.
The default name is `TONE` + pattern number (2 digits) + track number (2 digits), for example `TONE0103`.

## 2.5 Step encoding (8 bytes)

`[note, gate, volume, pan, KAOSS X, KAOSS Y, 7F, 00]`

| Field | Encoding |
|---|---|
| note | `octave·12 + semitone`, the same numbers as in the DS-10; `0xFF` = no note. The DSN-12 interface labels note 0 as `C-1` |
| gate | 0 = 25 %, 1 = 50 %, 2 = 75 % (default), 3 = 100 %, 4 = legato |
| volume | 16 levels: 0, 8, 17, 25, 34, 42, 51, 59, 68, 76, 85, 93, 102, 110, 119, 127 |
| pan | 0 = L2, 32 = L1, 64 = C, 95 = R1, 127 = R2 |
| KAOSS X, Y | 16 levels with the same values as volume; `0xFF` = not set |

An empty step is `FF 02 7F 40 FF FF 7F 00`; the second byte of an empty step is not significant.

## 2.6 Synthesizer parameters (40 × s16)

The first 31 parameters have the same order and meaning as the 31 parameters of the DS-10 (1.8), stored as `s16`. Parameters 31..39
exist only in the DSN-12; their default values are `0, 0, 0, 64, 0, 0, 0, 0, 0`. `VCO1 PW` (pulse width) is one of them; its exact
index is not decoded.

Drums and synthesizers use the same structure, so RELEASE (index 23) and PORTA (index 2) apply to every track, including drum tracks.

## 2.7 `mixs` — mixer

12 `mixr` chunks in channel order (Tr 01..Tr 12). Each has a `prms` chunk of 12 `u16` words:

| Word | Content |
|---|---|
| 0 | volume, `u7` |
| 1 | pan, `u7`: 0 = left, 64 = center, 127 = right |
| 2 | 0 (constant) |
| 3 | 1 (constant) |
| 4 | send to FX1, 0/1 |
| 5 | send to FX2, 0/1 |
| 6 | send to REVERB, 0/1 |
| 7 | MUTE, 0/1 |
| 8 | SOLO, 0/1 |
| 9..11 | 0 |

There is one mixer per song. By default no channel is sent to any effect.

## 2.8 `efcs` — effects

Three `efct` chunks: FX1, FX2 and REVERB, each with 80 `s16` words (`prms`). The words form 10 rows of 8 words; a row holds
the parameters of one effect type, and all rows are stored at the same time. Word 72 (row 9) selects the active type by row number.

| Row | Words | Type | Parameters |
|---|---|---|---|
| 0 | 0..4 | DELAY | SYNC (0 = OFF, 1 = BPM), TIME `u7`, L/R `s7`, FEEDBACK `s7`, DRY/WET `u7` |
| 1 | 8..10 | CHORUS | LFO FREQ, DEPTH, DRY/WET (`u7`) |
| 2 | 16..19 | FLANGER | LFO FREQ, DEPTH (`u7`), FEEDBACK (`s7`), DRY/WET (`u7`) |
| 3 | 24..26 | COMP | SENS, ATTACK, LEVEL (`u7`) |
| 4 | 32..34 | KICK | PUNCH, BOOST, LEVEL (`u7`) |
| 5 | 40..41 | HALL | TIME, LEVEL (`u7`) |
| 6 | 48..49 | ROOM | TIME, LEVEL (`u7`) |
| 7 | 56..57 | PLATE | TIME, LEVEL (`u7`) |
| 8 | 64..65 | SPRING | TIME, LEVEL (`u7`) |

Word 72 for FX1 and FX2: 0 = DELAY, 1 = CHORUS, 2 = FLANGER, 3 = COMP, 4 = KICK. Word 72 for REVERB: 5 = HALL, 6 = ROOM, 7 = PLATE,
8 = SPRING, 0 = DELAY (row 0, with SYNC and L/R). The layout of FX2 rows 2..4 and of row 8 was not observed directly and is
assumed to be the same as described. Unused words are 0.

## 2.9 `scns` — scenes and MUTE timeline

99 `scne` chunks. Scene `i` is song position `i` (from 0):

| Chunk | Size | Content |
|---|---|---|
| `numb` | 1 | scene number `i` |
| `patn` | 1 | pattern index 0..63, `FF` = empty scene |
| `mute` | 2 | `u16` mask of muted channels: bit `t` = 1 mutes channel Tr `t+1` (bit 0 = Tr 01); bits 12..15 unused |

The mute mask does not depend on the pattern of the scene (a scene without a pattern can have a mask).

## 2.10 `tons` and `oslo`

`tons` — the tone library of the song: 128 `synt` chunks (`prms` of 80 bytes and `name` of 16 bytes, as in 2.4 and 2.6).
`oslo` — a `prms` chunk of 32 bytes, not decoded.

## 2.11 Checksums

All checksums are CRC-32 with polynomial `0x04C11DB7`, most significant bit first (no reflection), final XOR `0xFFFFFFFF`. They differ
in the initial value and the data range:

| File | Field | Initial value | Data |
|---|---|---|---|
| `.sng` | `0x1C` | `0x64737864` | bytes from `0x20` to the end of the file (the LZ11 stream) |
| `database_v1.dat` | `0x08` | `0x6473666D` (`mfsd` as a little-endian `u32`) | bytes from `0x10` to the end of the file, including the final `00` |

## 2.12 Catalog `database_v1.dat`

| Offset | Size | Content |
|---|---|---|
| `0x00` | 4 | signature `mfsd` |
| `0x04` | u32 | file size |
| `0x08` | u32 | CRC-32 (see 2.11) |
| `0x0C` | u32 | 0 |
| `0x10` | | ASCII text: one line `{ "NAME", "NAME.sng" },` followed by a line feed for each song, then a final `00` |
