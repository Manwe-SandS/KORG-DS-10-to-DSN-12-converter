# KORG DS-10 → DSN-12 converter

Converts songs from a KORG DS-10 save file (Nintendo DS, `.sav`) into KORG DSN-12 songs (Nintendo 3DS).
It works in one direction only: DS-10 → DSN-12.

## Requirements

Python 3 (tested on 3.9). No other dependencies.

## Usage

```
python3 ds10_to_dsn12.py "DS-10 input.sav" [-o folder]
```

| Argument | Description |
|---|---|
| `DS-10 input.sav` (required) | the KORG DS-10 save file to convert |
| `-o folder` | the folder for the result (default: `dsn12_out`) |
| `--songs 1,3` | convert only these songs; numbers are the same as in the DS-10 interface, starting from 1 (default: all non-empty songs) |

A DS-10 save file can hold several songs. Each song becomes a file `NAME.sng` in `folder`, and a catalog `database_v1.dat` listing these songs is created next to them. If two songs have the same name, a digit is appended to the name.

## Copying to the 3DS

Load the resulting files into the KORG DSN-12 with the **Checkpoint** application, running on the game console itself. Backup your old data first.

## What is converted

- song name, tempo, swing, the sequence of patterns, the number of steps in each pattern;
- the notes of all six parts (SYN1, SYN2, DRUM1..4): pitch, gate length, volume, pan, KAOSS X/Y;
- synthesizer and drum parameters, tone names, KAOSS octave and assignments;
- mixer: volume and pan of each channel;
- the DS-10 effect (Delay, Chorus or Flanger, with all its parameters) goes to **FX1** of the DSN-12; the DS-10 `FX ASSIGN` setting decides which channels are sent to FX1;
- the **MUTE** timeline goes to the scene mute masks of the DSN-12.

## DSN-12 channel layout

| DSN-12 channel | Content |
|---|---|
| 1, 2 | SYN1, SYN2 |
| 3, 4, 5, 6 | DRUM1, DRUM2, DRUM3, DRUM4 |
| 7–12 | empty |

The layout is fixed and follows the order of the parts on the DS-10.

## Good to know

- **Drums.** DS-10 drums have no RELEASE and PORTA knobs, but the DSN-12 has them, so for drums RELEASE is set to the middle of the scale (64) and PORTA to 0. For synthesizers both parameters are copied as they are. After converting a tune, it is worth checking the drum sound by ear and, if necessary, adjusting the ATTACK and RELEASE parameters by hand.
- **Note volume and KAOSS.** The DS-10 editor grid has 12 levels, the DSN-12 grid has 16; the values are rescaled proportionally.
- **One mixer and one effect per song.** The DS-10 stores them separately in every pattern, the DSN-12 stores them once per song. The settings of the pattern of the first scene are used.
- **FX2 and Reverb.** The DS-10 has no such effects, so they stay as in the built-in reference DSN-12 song, and no channel is sent to them.
- **Patterns.** The DS-10 has 16 patterns; the remaining DSN-12 patterns (up to 64) stay empty.

## Warnings

Warnings are printed to the console with the prefix `WARNING:`. The conversion still completes and the files are created.

| Message                                                      | Meaning                                                      |
|--------------------------------------------------------------|--------------------------------------------------------------|
| `the DS-10 mixer differs between patterns`, `the DS-10 effect differs between patterns` | the settings of the pattern of the first scene were used, the others were ignored |
| `the 100th song position is used`                            | the DSN-12 has only 99 scenes, the last position was dropped |
| `unknown DS-10 effect type`, `unknown FX ASSIGN value`       | a corrupted or unexpected value: Delay is used, or the effect is not routed to any channel |
| `the MD5 of song N does not match`, `the MD5 of the file header does not match` | the DS-10 save file is damaged, the data may be wrong        |

If the input is not a DS-10 save file, the converter stops with an `error:` message.

## Limitations

- The DS-10 dual mode (SYN3/4, DRUM5..8) is not supported and has not been tested.
- The reverse conversion (DSN-12 → DS-10) is not implemented.

## Further reading

`KORG_DS_format.md` — a description of the KORG DS-10 save file and the KORG DSN-12 song file formats.

## Sound

The DS-10 gives a somewhat fatter and bassier sound than the DSN-12, so the same song may sound a little thinner after conversion. The console also matters: the amplifier of the Nintendo 3DS XL reproduces bass better than the amplifier of the New Nintendo 3DS XL.

## Contact

manwe@demoscene.ru
