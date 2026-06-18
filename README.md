SuperFW (WenQuanYi fork)
========================

This fork tracks upstream `davidgfnet/superfw` and keeps a narrow set of
differences:

- WenQuanYi 10pt as the primary UI font
- Fusion Pixel BDF as fallback glyph coverage during font generation
- `NO_SD_MODE=1` for emulator-oriented builds
- Chinese as the default UI language

Upstream project:
https://github.com/davidgfnet/superfw

Build examples:

```bash
# Hardware
make BOARD=chis BUNDLE_GBC_EMULATOR=0 BUNDLE_OTHER_EMULATORS=0

# Emulator / no SD card init
make BOARD=chis NO_SD_MODE=1 BUNDLE_GBC_EMULATOR=0 BUNDLE_OTHER_EMULATORS=0
```

The rest of this document is the original upstream README.

Detailed fork maintenance notes for future AI agents and maintainers are in
`FORK_MERGE_GUIDE.md`.

---

SuperFW
=======

An alternative firmware for Supercard GBA flash carts

This project aims to provide a more modern and better firmware for Supercard
flash carts (which are still widely used and very cheaply available). The goal
is to add many features only present in more expensive or sophisticated flash
carts. Unfortunately we are limited to the actual hardware so certain features
are impossible or very complex to implement.

Find the website and documentation at https://superfw.davidgf.net/


Installation
------------

Check https://superfw.davidgf.net/docs/install/flash/ for more details.

The firmware can be chain-loaded using another firmware (ie. the default
SuperCard firmware or SCFW) and loaded as a regular game. It can also be
installed on the internal flash device. Installing it enables some nice
features such as SDHC and exFAT compatibility.

To install the firmware you can simply load it first, and then use SuperFW
to flash itself on the flash. You will need to enable flashing in the Info
tab and then pick the .fw file and flash it. It is strongly recommended to
reboot your GBA after flashing.
