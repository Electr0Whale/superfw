# GBA Cover Pack Usage

This release includes `superfw-covers.pak`, a prebuilt GBA box-art package for
SuperFW's cover browser.

1. Install the matching SuperFW firmware from this release.
2. Create a `.superfw` directory at the root of the SD card if it does not
   already exist.
3. Copy `superfw-covers.pak` to `/.superfw/covers.pak` on the SD card.
4. Restart the console. Covers are shown when a ROM's four-character GBA game
   code is present in the package.

The package is optional. A missing or invalid package only disables cover
display; it does not prevent games from launching.

The pack in this release contains 2,915 game-code entries, including aliases
for the verified Chinese ROM collection mappings. ROMs with invalid game-code
headers cannot be matched by the firmware and are intentionally not included.
