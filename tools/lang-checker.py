#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Generate font using:
#   python3 bdf_to_pack.py --bdf fusion-pixel-zh_hans.bdf --output ../fonts.pack
# Or for full:
#   python3 bdf_to_pack.py --bdf fusion-pixel-zh_hans.bdf --output ../fonts-full.pack

import json, struct, os, sys

if hasattr(sys.stdout, "reconfigure"):
  sys.stdout.reconfigure(encoding="utf-8")

# Load font from pack directly, and parse it to generate a char-width table
charw = {}

fontpath = sys.argv[1] if len(sys.argv) > 1 else "res/fonts.pack"
fdata = open(fontpath, "rb").read()
m1, m2, ver, bk, size = struct.unpack("<BBBBI", fdata[:8])
fdata = fdata[8:]

assert m1 == 70 and m2 == 79 and ver == 1
CHAR_SPACING = 1    # Must match font_render.c

FLAG_FW16 = 0x00000001
FLAG_FW12 = 0x00000002

dataoff = bk * 16
for i in range(bk):
  start, end, flgs, off = struct.unpack("<IIII", fdata[i*16:(i+1)*16])
  if flgs & FLAG_FW16:
    # Fixed 16 pixels wide (legacy).
    for c in range(start, end+1):
      charw[c] = 16
  elif flgs & FLAG_FW12:
    # Fixed 12 pixels wide (Fusion Pixel CJK).
    for c in range(start, end+1):
      charw[c] = 12 + CHAR_SPACING
  else:
    # Variable width (Latin, 1-8 columns).
    for i, c in enumerate(range(start, end+1)):
      idx = struct.unpack("<H", fdata[dataoff + i * 2 : dataoff + i * 2 + 2])[0]
      charw[c] = (idx >> 12) + 1 + CHAR_SPACING


# Attempts to do some sanity checking with translations
alerts = list(eval("{" + "\n".join([x for x in open("res/messages.py", encoding="utf-8").read().split("\n") if "# alertmsg" in x]) + "\n}").keys())
igmalerts = list(eval("{" + "\n".join([x for x in open("res/messages.py", encoding="utf-8").read().split("\n") if "# igm-alertmsg" in x]) + "\n}").keys())

# Check that all unicode chars are in the font file!
print("------- FONT FILE CHECK -------")
for f in os.listdir("res/lang/"):
  data = json.load(open("res/lang/" + f, encoding="utf-8"))
  for k in data:
    if not data[k]: continue
    for x in data[k]:
      if ord(x) not in charw:
        print(k, f, "character", hex(ord(x)), x)

# Check if pop up messages fit within the space allocated
POPUP_WIDTH = 206      # In pixels

print("------- POP UP STRING CHECK -------")
for f in os.listdir("res/lang/"):
  data = json.load(open("res/lang/" + f, encoding="utf-8"))
  for k in alerts:
    if k in data:
      sentlen = sum(charw[ord(x)] for x in data[k])
      if sentlen > POPUP_WIDTH:
        print(k, f, data[k], sentlen)

POPUP_WIDTH = 234      # In pixels

print("------- IGM POP UP STRING CHECK -------")
for f in os.listdir("res/lang/"):
  data = json.load(open("res/lang/" + f, encoding="utf-8"))
  for k in igmalerts:
    if k in data:
      sentlen = sum(charw[ord(x)] for x in data[k])
      if sentlen > POPUP_WIDTH:
        print(k, f, data[k], sentlen)



