#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import subprocess
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PRIMARY = os.path.join(ROOT, "wenquanyi_10pt.bdf")
SECONDARY = os.path.join(ROOT, "fusion-pixel-12px-monospaced-zh_hans.bdf")
OUT_PACK = os.path.join(ROOT, "res", "fonts.pack")
OUT_EXT = os.path.join(ROOT, "res", "fonts-ext.pack")
OUT_EMBED = os.path.join(ROOT, "src", "fonts", "font_embed.h")
COMBINE = os.path.join(THIS_DIR, "combine_fonts.py")
PACK_TO_CARRAY = os.path.join(THIS_DIR, "pack_to_carray.py")


def run(cmd):
    subprocess.run(cmd, check=True)


def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        embed_pack = os.path.join(tmpdir, "font_embed.pack")

        run([
            sys.executable, COMBINE,
            "--primary", PRIMARY,
            "--secondary", SECONDARY,
            "--output", OUT_PACK,
            "--embed-output", embed_pack,
        ])
        with open(OUT_EMBED, "w", encoding="utf-8", newline="\n") as out:
            subprocess.run(
                [sys.executable, PACK_TO_CARRAY, embed_pack],
                check=True,
                stdout=out,
            )
        run([
            sys.executable, COMBINE,
            "--primary", PRIMARY,
            "--secondary", SECONDARY,
            "--output", OUT_EXT,
        ])


if __name__ == "__main__":
    main()
