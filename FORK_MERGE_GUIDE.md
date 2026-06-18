# SuperFW WenQuanYi Fork: AI Maintenance and Merge Guide

This document is the primary handoff file for future AI agents and maintainers
working on `Electr0Whale/superfw`.

Read this file before editing code. It records the purpose of this fork, the
intended long-term delta from upstream `davidgfnet/superfw`, the font
implementation, the emulator build path, the verified UI fixes, and the merge
strategy for future upstream updates.

The key maintenance goal is narrow:

> Keep this fork as close to upstream SuperFW as possible while preserving the
> WenQuanYi/Fusion Pixel font stack, `NO_SD_MODE=1`, Chinese default language,
> and the small UI baseline fixes required by the new font.

## 1. Repository Identity

- Upstream project: `https://github.com/davidgfnet/superfw`
- Fork project: `https://github.com/Electr0Whale/superfw`
- Local working repo used during this handoff:
  `C:\Users\Admin\Documents\superFW_fork`
- Branch observed during this handoff: `wqy10pt-font`
- Main built artifact for emulator verification:
  `C:\Users\Admin\Documents\superFW_fork\superfw.gba`

SuperFW is an alternative firmware for Supercard GBA flash carts. This fork is
not intended to become a broad feature fork. It exists to provide better CJK
font coverage and an emulator-friendly development build while staying easy to
merge with upstream.

## 2. Golden Rules For Future AI Agents

1. Preserve only intentional fork differences.
2. Prefer upstream code for unrelated firmware behavior.
3. Keep font changes localized to font generation, font rendering, and the
   small menu baseline fixes documented here.
4. Do not modify loader, ROM boot, or in-game-menu font ABI unless upstream
   changes make that necessary.
5. Do not reintroduce debug scripts, screenshots, `.sav` files, local binaries,
   editor files, or agent state files into committed history.
6. When testing visuals, do not automate mGBA key presses unless the user asks.
   The user prefers manual screenshots. If image judgment is required, ask the
   user to switch to a model that can inspect images.
7. On Windows, use devkitPro MSYS2 for firmware builds. Do not assume ordinary
   PowerShell has the ARM toolchain configured.
8. Avoid PowerShell redirection for generated C headers containing UTF-8 text.
   Use MSYS2 shell redirection or Python with explicit UTF-8.

## 3. Intended Long-Term Fork Delta

The fork should remain describable in one sentence:

> Upstream SuperFW plus WenQuanYi 10pt/Fusion Pixel font packs, `NO_SD_MODE=1`,
> Chinese default language, and minimal visual baseline fixes for that font.

### Must Keep

- Font sources:
  - `wenquanyi_10pt.bdf`
  - `fusion-pixel-12px-monospaced-zh_hans.bdf`
- Font outputs:
  - `res/fonts.pack`
  - `res/fonts-ext.pack`
  - `src/fonts/font_embed.h`
- Font build entry:
  - `res/fonts/build.py`
- Runtime font support:
  - `FLAG_FW12`
  - variable-width `12-bit offset / 4-bit width` index decoding
- Emulator mode:
  - `NO_SD_MODE=1` in `Makefile`
  - bounded `#ifdef NO_SD_MODE` path in `src/main.c`
- Default language:
  - `src/settings.c`: `uint32_t lang_id = 1;`
  - `res/messages.py`: `OTHER_LANGS` keeps `zh` first after English
- Menu visual fixes verified by user:
  - safe triangle glyphs for scroll arrows
  - `< ... >` bracket baseline split
  - save button text nudge
  - tools/test-page left pointer nudge

### Should Usually Follow Upstream

- `rom_boot.S`
- `src/loader.c`
- `src/ingame.S`
- `src/ingame.h`
- most of `src/menu.c`
- most of `src/ingame_menu.c`
- upstream message keys and translation framework
- README structure after the short fork notice
- CI/toolchain improvements

### Should Not Be Long-Term Fork Delta

- PCF font source path
- `FONT_SOURCE` / `FONT_INPUT` public build switches
- old `wenquanyi_9pt.*` files
- debug scripts such as `_debug_*.py`, `_check*.py`, capture scripts
- screenshots and visual probe images
- `.sav` result files
- local helper binaries such as `chismaker.exe`
- agent/editor/workflow noise such as `skills-lock.json`
- broad UI rewrites unrelated to font rendering
- new fork-only runtime features not requested by the user

## 4. Current User-Verified UI State

The user manually verified the final UI behavior in mGBA. Do not undo these
fixes without re-testing visually.

### Settings Option Values

Problem:

- In settings screens, option values formatted like `< ... >` looked vertically
  off after moving the whole string.
- The final desired behavior is not to raise the whole string.

Final implementation:

- `src/menu.c` has `draw_central_option_text()`.
- If text starts with `<` and ends with `>`, it splits into three renders:
  - `<` at `y - 1`
  - inner text at `y`
  - `>` at `y - 1`
- The whole group is horizontally centered by total width.

Important coordinate detail:

```c
unsigned left = x >= tw / 2 ? x - tw / 2 : 0;
uint8_t *basept = (uint8_t*)&frame[y * SCREEN_WIDTH + left];
draw_text_idx8_bus16("<", basept - SCREEN_WIDTH, SCREEN_WIDTH, FT_COLOR);
draw_text_idx8_bus16(inner, basept + lw, SCREEN_WIDTH, FT_COLOR);
draw_text_idx8_bus16(">", basept + lw + iw - SCREEN_WIDTH, SCREEN_WIDTH, FT_COLOR);
```

Do not replace this with `draw_central_text(t, frame, x, y - 1)`. That was the
intermediate bug: it raised the inner text too.

### Save Button Text

Problem:

- The global/UI settings "Save to SD card" button text looked slightly low.

Final implementation:

- `draw_central_text_nudge_up()` calls `draw_central_text(..., y - 1)`.
- It is used for save button text only.

### Scroll Arrows

Problem:

- Some arrow glyphs rendered as garbage because the selected Unicode arrows were
  not present in the generated font pack.

Verified font-pack facts:

- Missing in `res/fonts.pack`:
  - `U+2191` up arrow
  - `U+2193` down arrow
  - `U+2BC5`
  - `U+2BC6`
- Present in `res/fonts.pack`:
  - `U+25B2` black up triangle
  - `U+25BC` black down triangle
  - `U+25B4` black up small triangle
  - `U+25BE` black down small triangle
  - `U+25C0` black left triangle
  - `U+25B6` black right triangle

Final implementation:

- Settings scroll indicators use `▴` and `▾`.
- Keep these unless the font pack is regenerated with verified support for the
  old symbols.

### Tools/Test Page Left Pointer

Problem:

- The left animated pointer in the test/tools page looked too low.
- A previous attempt moved `render_icon_trans(..., 63)` by `-2`, but that object
  is the 16x16 selector highlight strip, not the pointer glyph. That caused
  settings text to look low and did not move the pointer.

Final implementation:

- Restore settings selector highlight positions:
  - global settings: `offy + (selector - baseopt) * 20`
  - UI/language settings: `22 + selector * 20`
- Move only the tools pointer:

```c
draw_central_text("▸", frame, 11 + (smenu.anim_state >> 6),
                  24 + 22 * smenu.tools.selector);
```

Do not move `icon 63` to solve pointer positioning. `icon 63` is generated in
`menu_init()` with `SEL_COLOR` and is used as a translucent selection strip.

## 5. Font Pack Format

The fork keeps upstream's outer `FO v1` font pack structure:

```c
typedef struct {
  char magic[2];        // "FO"
  uint8_t version;      // 1
  uint8_t block_count;
  uint32_t data_size;
  t_charblock_info charblks[];
} t_charblock_header;
```

The fork extends interpretation through block flags and variable-width index
encoding. It does not change the outer header ABI.

### Runtime Flags

Defined in `src/fonts/font_render.c`:

```c
#define FLAG_FW16     0x00000001
#define FLAG_FW12     0x00000002
#define FLAG_COMP     0x80000000
```

Runtime modes:

- `FLAG_COMP`: upstream Hangul composition mode, 16-column glyph components.
- `FLAG_FW16`: fixed 16-column glyphs.
- `FLAG_FW12`: fork fixed 12-column glyphs.
- no fixed flag: variable-width glyphs with packed width/offset index.

### `FLAG_FW12`

`FLAG_FW12` is the most important runtime addition for this fork.

Behavior:

```c
chinfo->char_width = 12;
chinfo->spacing_cols = 1;
chinfo->nchars = 1;
chinfo->data[0] = &chptr[12 * code_offset];
```

Use it for CJK-style blocks where 12 columns match the WenQuanYi 10pt visual
target.

### Variable-Width Index Encoding

For variable-width blocks, each 16-bit index entry is:

```text
bits 15..12: width - 1
bits 11..0 : column-data offset, unit = uint16_t
```

Runtime decoding:

```c
uint16_t ientry = chptr[code_offset];
chinfo->char_width = (ientry >> 12) + 1;
chinfo->data[0] = &chdata[ientry & 0xFFF];
```

Consequences:

- Maximum encoded width is 16 columns.
- Current generator usually caps Latin-ish variable glyphs at 12 columns.
- Offset limit is 4095 `uint16_t` columns per variable-width block.
- If future fonts exceed this, change the generator or split blocks before
  changing runtime ABI.

## 6. Font Generation

There is one standard font build entry:

```bash
python3 res/fonts/build.py
```

Inputs:

- `wenquanyi_10pt.bdf`
- `fusion-pixel-12px-monospaced-zh_hans.bdf`

Outputs:

- `res/fonts.pack`
- `res/fonts-ext.pack`
- `src/fonts/font_embed.h`

Helper scripts:

- `res/fonts/bdf_to_pack.py`
- `res/fonts/combine_fonts.py`
- `res/fonts/pack_to_carray.py`

`build.py` intentionally writes the embedded font in two stages:

1. `combine_fonts.py --embed-output <temp>.pack`
2. `pack_to_carray.py <temp>.pack > src/fonts/font_embed.h`

Do not pass `src/fonts/font_embed.h` directly as `--embed-output`.
`combine_fonts.py` writes a binary pack there, not a C header.

`src/fonts/font_embed.h` must look like a C array:

```c
#include <stdint.h>

const uint32_t font_ascii_embedded[] = {
  ...
};
```

It must not start with raw `FO` bytes.

## 7. Build System

### Key Makefile Variables

Keep:

```make
export PYTHONIOENCODING = utf-8

FONT_PACK = res/fonts.pack
FONT_EXT_PACK = res/fonts-ext.pack
FONT_EMBED = src/fonts/font_embed.h

NO_SD_MODE ?= 0
```

Keep font dependencies attached to `firmware.ewram.gba`:

```make
firmware.ewram.gba: ... $(FONT_PACK) $(FONT_EXT_PACK) $(FONT_EMBED)
```

Keep the generation rule:

```make
$(FONT_PACK) $(FONT_EXT_PACK) $(FONT_EMBED): res/fonts/build.py \
		wenquanyi_10pt.bdf fusion-pixel-12px-monospaced-zh_hans.bdf \
		res/fonts/bdf_to_pack.py res/fonts/combine_fonts.py res/fonts/pack_to_carray.py
	./res/fonts/build.py
```

Remove or reject during merge:

- `FONT_SOURCE`
- `FONT_INPUT`
- PCF as standard build path
- multiple official font generation entrypoints

### Hardware Build

MSYS2/devkitPro command used successfully on Windows:

```powershell
& 'C:\devkitPro\msys2\msys2_shell.bat' -defterm -no-start -here -c "cd /c/Users/Admin/Documents/superFW_fork && make BOARD=chis BUNDLE_GBC_EMULATOR=0 BUNDLE_OTHER_EMULATORS=0"
```

### Emulator / No-SD Build

```powershell
& 'C:\devkitPro\msys2\msys2_shell.bat' -defterm -no-start -here -c "cd /c/Users/Admin/Documents/superFW_fork && make BOARD=chis NO_SD_MODE=1 BUNDLE_GBC_EMULATOR=0 BUNDLE_OTHER_EMULATORS=0"
```

### Clean Emulator Build

Use this when testing UI fixes or suspecting stale outputs:

```powershell
& 'C:\devkitPro\msys2\msys2_shell.bat' -defterm -no-start -here -c "cd /c/Users/Admin/Documents/superFW_fork && make clean && make BOARD=chis NO_SD_MODE=1 BUNDLE_GBC_EMULATOR=0 BUNDLE_OTHER_EMULATORS=0"
```

Expected final artifact:

```text
C:\Users\Admin\Documents\superFW_fork\superfw.gba
```

Current environment may print:

```text
Creating /dev/shm directory failed.
Creating /dev/mqueue directory failed.
```

These are non-fatal in the observed Windows/MSYS2 environment if `make` exits
with code 0.

Common non-blocking warnings:

- `src/menu.c: warning: unused variable 'err'`
- linker warning: `LOAD segment with RWX permissions`
- `src/res/logo.h: init_logo_palette defined but not used`

Do not start a broad refactor just to silence these warnings.

## 8. `NO_SD_MODE=1`

`NO_SD_MODE` is an emulator/debug build mode, not a new runtime feature.

Makefile behavior:

- adds `-DNO_SD_MODE`
- disables firmware compression
- raises `MAXFSIZE` to 4096 KiB
- keeps build independent of SD/Supercard hardware initialization

`src/main.c` behavior:

- skips `set_supercard_mode`
- skips `init_sdcard_and_mount`
- skips pending save and SRAM test hardware path
- initializes `pdbinfo`
- scans ROM for the real `fonts.pack`

Important scanner detail:

```c
const uint8_t *scan = (const uint8_t*)0x08040000;
const uint8_t *end  = (const uint8_t*)0x08400000;
...
if (scan[0] == 'F' && scan[1] == 'O' && scan[2] == 1) {
  uint32_t dsize = *(const uint32_t*)(scan + 4);
  if (dsize > 500000) {
    font_base_addr = (void*)scan;
    break;
  }
}
```

The `dsize > 500000` threshold avoids picking up the small embedded font pack.

Future merges:

- keep the normal hardware path as close to upstream as possible.
- keep the no-SD path contained in `src/main.c` and `Makefile`.
- do not spread emulator conditionals into menu, loader, save, or flash logic.

## 9. Languages And Messages

`res/messages.py` controls language order.

Current order:

```python
OTHER_LANGS = [
  "zh",
  "ja",
  "ru",
  "id",
  "ms",
]
```

`src/settings.c` uses:

```c
uint32_t lang_id = 1;  // zh (Chinese) as default (en=0, zh=1)
```

If upstream changes message generation or language ordering, verify that
`lang_id = 1` still means Chinese. If not, adjust either the order or default
value while preserving Chinese as default.

### Message Header Generation

Use MSYS2 shell redirection:

```bash
./res/messages.py h main > src/messages_data.h
./res/messages.py h menu > src/menu_messages.h
```

Avoid Windows PowerShell `>` for these generated C headers because it may emit
UTF-16. UTF-16 headers will break C compilation or corrupt strings.

`res/messages.py` and `tools/lang-checker.py` should read/write UTF-8 explicitly
on Windows CJK locales.

## 10. Encoding Rules

This repo intentionally contains UTF-8 text:

- Chinese translations
- Japanese translations
- Russian translations
- UI symbols such as `▸`, `▴`, `▾`, `◀`, `▶`, `⯇`, `⯈`, `⯅`, `⯆`

Rules:

- Source and generated headers must be UTF-8.
- Prefer no BOM for C and Python files.
- Do not let PowerShell create UTF-16 files.
- When editing with scripts, explicitly specify UTF-8.

Quick checks:

```powershell
Format-Hex src\menu_messages.h | Select-Object -First 4
Format-Hex src\messages_data.h | Select-Object -First 4
Format-Hex src\fonts\font_embed.h | Select-Object -First 4
```

Bad signs:

- `FF FE` at file start: UTF-16 LE
- raw `46 4F 01` at `src/fonts/font_embed.h` start: binary font pack in a C header slot
- visible mojibake such as `鈻?`

## 11. Current File-Level Difference Map

Use this section to judge merge conflicts.

### `.github/workflows/build.yml`

Intentional change:

- CI font generation uses:

```yaml
python3 res/fonts/build.py
```

Merge policy:

- accept upstream CI/toolchain improvements.
- keep the single font generation command.

### `Makefile`

Intentional changes:

- `PYTHONIOENCODING=utf-8`
- font output variables
- font generation rule
- `NO_SD_MODE`
- `emu` convenience target

Merge policy:

- accept upstream build improvements.
- reattach font outputs to firmware dependencies.
- preserve no-SD build switch.
- do not restore PCF/FONT_SOURCE as public interface.

### `README.md`

Intentional shape:

- short fork notice at top.
- original upstream README below.

Merge policy:

- keep README mostly upstream.
- avoid long fork marketing text.
- put detailed fork maintenance information in this file instead.

### `res/fonts/README.md`

Documents only:

- standard font build command.
- primary/fallback BDF sources.
- generated outputs.

### `res/fonts/build.py`

Intentional fork entrypoint:

- single standard font build path.
- emits both runtime packs and embedded C array.

### `res/fonts/bdf_to_pack.py`

Intentional changes:

- masks shifted columns with `& 0xFFFF`.
- masks fixed-block column writes with `& 0xFFFF`.

Reason:

- BDF parsing and shifting can otherwise exceed `uint16_t` range.

### `res/fonts/combine_fonts.py`

Intentional changes:

- primary/fallback glyph merge.
- writes `FLAG_FW12` for 12-column fixed blocks.
- masks columns with `& 0xFFFF`.
- keeps Latin-ish blocks variable-width when possible.

Merge policy:

- preserve pack semantics documented in section 5.

### `src/fonts/font_render.c`

Intentional runtime changes:

- `FLAG_FW12`
- `FLAG_FW12` lookup/render info path
- variable-width index decoding with high nibble width and low 12-bit offset

Merge policy:

- this is a high-risk conflict file.
- inspect manually after upstream merges.
- preserve external API unless upstream changed it.

### `src/main.c`

Intentional change:

- bounded `NO_SD_MODE` startup path.

Merge policy:

- keep normal hardware path aligned with upstream.
- reapply no-SD branch in one place.

### `src/settings.c`

Intentional changes:

- default language is Chinese.
- some hotkey arrow symbols may be changed to glyphs covered by this font.

Merge policy:

- verify symbol coverage before accepting/reverting arrow glyph changes.
- keep `lang_id` default Chinese.

### `src/menu.c`

Intentional current fork differences:

- `draw_central_option_text()`
- `draw_central_text_nudge_up()`
- safe scroll arrow glyphs `▴` / `▾`
- save button text y nudge
- tools pointer y nudge

Merge policy:

- generally accept upstream menu logic.
- preserve only the small font-baseline fixes above.
- do not move selector highlight strips to fix text or pointer alignment.
- keep `render_icon_trans(..., 63)` semantics as selection background.

### `src/ingame_menu.c`

Merge policy:

- usually accept upstream.
- only adjust if font API or ABI changes require it.

### `res/lang/*.json`

Intentional fork assets:

- Chinese, Japanese, Russian, Indonesian, Malay language resources.

Merge policy:

- add upstream new keys to all language JSON files.
- delete removed keys if no longer used.
- avoid full reformatting unless necessary.

### `tools/lang-checker.py`

Intentional changes:

- UTF-8 safe reads/stdout on Windows.

Use:

```bash
python3 tools/lang-checker.py res/fonts.pack
```

The script may print width warnings. Treat exit code as the primary pass/fail
signal unless explicitly investigating layout.

## 12. Worktree Hygiene Before Commit

At the time of this handoff, the worktree contained useful code/doc changes and
also many debug artifacts. Before committing, future agents should classify
files carefully.

Likely intended long-term assets:

- `.github/workflows/build.yml`
- `Makefile`
- `README.md`
- `FORK_MERGE_GUIDE.md`
- `res/fonts.pack`
- `res/fonts-ext.pack`
- `res/fonts/README.md`
- `res/fonts/build.py`
- `res/fonts/bdf_to_pack.py`
- `res/fonts/combine_fonts.py`
- `res/fonts/pack_to_carray.py`
- `res/lang/*.json`
- `res/messages.py`
- `src/fonts/font_embed.h`
- `src/fonts/font_render.c`
- `src/main.c`
- `src/menu.c`
- `src/messages_data.h`
- `src/settings.c`
- `tools/lang-checker.py`
- `wenquanyi_10pt.bdf`
- `fusion-pixel-12px-monospaced-zh_hans.bdf`

Likely should not be committed:

- `_mgba_keys.ps1`
- `_win32input_probe.cs`
- `_checkw.py`
- `_debug_cjk.py`
- `capture_*.ps1`
- `mgba_*.png`
- `superfw-*.png`
- `results/*.sav`
- `screenshots/*.png`
- `screenshots/*.jpg`
- `chismaker.exe`
- `skills-lock.json`
- `*.elf`
- `*.map`
- `*.payload`
- `*.comp`
- `superfw.gba`, unless user explicitly wants binary release artifacts tracked

Deleted old assets that should generally stay deleted:

- `res/fonts/pcf_to_pack.py`
- `wenquanyi_9pt.bdf`
- `wenquanyi_9pt.pcf`

## 13. Upstream Merge Procedure

Recommended command flow:

```bash
git remote add upstream https://github.com/davidgfnet/superfw.git
git fetch upstream
git checkout wqy10pt-font
git checkout -b merge-upstream-YYYYMMDD
git merge upstream/master
```

Conflict resolution order:

1. `Makefile` and CI.
2. font scripts and generated font outputs.
3. `src/fonts/font_render.c`.
4. `src/main.c`.
5. message generation and language JSON.
6. `src/menu.c` visual fixes.
7. README/docs.

Conflict decision rule:

1. Preserve upstream firmware correctness.
2. Preserve the intentional fork delta.
3. Preserve reproducible generated outputs.
4. Preserve user-verified visual fixes.
5. Drop unrelated fork drift.

## 14. Required Validation After Merge

Run font generation:

```bash
python3 res/fonts/build.py
```

Run font coverage/layout check:

```bash
python3 tools/lang-checker.py res/fonts.pack
```

Run hardware build:

```bash
make BOARD=chis BUNDLE_GBC_EMULATOR=0 BUNDLE_OTHER_EMULATORS=0
```

Run emulator/no-SD build:

```bash
make BOARD=chis NO_SD_MODE=1 BUNDLE_GBC_EMULATOR=0 BUNDLE_OTHER_EMULATORS=0
```

On Windows, run these through devkitPro MSYS2 for the `make` steps.

Manual runtime checks:

- boot mGBA with `superfw.gba`.
- verify Chinese default language.
- switch languages and verify English, Chinese, Japanese, Russian display.
- check global settings screen.
- check UI/language settings screen.
- check settings screens with `▴` / `▾` scroll indicators.
- check `< ... >` values: brackets raised, inner text not raised.
- check "Save to SD card" button text alignment.
- check tools/test page left `▸` pointer alignment.
- check file browser text truncation.
- check popup text.
- check GBA loading/patching option screens if possible.

If screenshot inspection is needed:

- ask the user to take a manual screenshot.
- ask the user to switch to a model that can inspect images.
- do not rely on automated mGBA key scripts unless explicitly requested.

## 15. Known Debugging Lessons

### Clean Build Matters

When UI changes seem not to appear, run:

```powershell
& 'C:\devkitPro\msys2\msys2_shell.bat' -defterm -no-start -here -c "cd /c/Users/Admin/Documents/superFW_fork && make clean && make BOARD=chis NO_SD_MODE=1 BUNDLE_GBC_EMULATOR=0 BUNDLE_OTHER_EMULATORS=0"
```

Then verify `superfw.gba` timestamp.

### mGBA File Lock

If mGBA has the ROM open, `objcopy` may fail with:

```text
arm-none-eabi-objcopy.exe: superfw.gba: Invalid argument
```

Close mGBA before rebuilding.

### Arrow Glyph Coverage

Do not assume Unicode arrows exist in the font pack. Check `res/fonts.pack`
before changing symbols.

### Selector Highlight vs Pointer Glyph

`render_icon_trans(..., 63)` renders a generated 16x16 selector strip. It is not
the animated `▸` pointer. Moving it changes highlight position, not pointer
baseline.

## 16. Suggested Prompt For Future AI Merge Work

Use this prompt with the repository and this file:

```text
You are maintaining Electr0Whale/superfw, a fork of davidgfnet/superfw.
Read FORK_MERGE_GUIDE.md first. Merge the latest upstream changes while keeping
the fork delta narrow.

Preserve:
- WenQuanYi 10pt + Fusion Pixel BDF font generation.
- res/fonts/build.py as the single standard font entry.
- FO v1 runtime support for FLAG_FW12 and 12-bit offset / 4-bit width variable
  indices.
- NO_SD_MODE=1 in Makefile and the bounded src/main.c branch.
- Chinese default language.
- documented menu baseline fixes for the new font.

Prefer upstream for all unrelated firmware behavior. Do not commit debug
scripts, screenshots, .sav files, local binaries, or generated build outputs
unless the repo explicitly tracks them. After merging, run font generation,
lang-checker, hardware build, and NO_SD_MODE build.
```

## 17. Completion Checklist For Future Agents

Before declaring work complete:

- [ ] This file still matches the code.
- [ ] `res/fonts/build.py` regenerates all font outputs.
- [ ] `src/fonts/font_embed.h` is a C array.
- [ ] `src/fonts/font_render.c` still supports `FLAG_FW12`.
- [ ] `NO_SD_MODE=1` build passes.
- [ ] normal `BOARD=chis` build passes.
- [ ] Chinese remains default language.
- [ ] settings/UI baseline fixes still render correctly.
- [ ] no debug screenshots/scripts are staged.
- [ ] no UTF-16 generated headers are present.
- [ ] no accidental BOM was added to C/Python source files.

