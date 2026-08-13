#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright 2024 David Guillen Fandos <david@davidgf.net>

# Testing tool to patch games with the in-game menu payload (for testing!)
# It works like this:
#  - Get a ROM, an ingame-menu payload and a font pack.
#  - Patch the font pack and in-game menu, with required fields.
#  - Patch the ROM entry point and add some ROM power-of-two padding.
#
# Note: the ROM must be already patched for IRQ handler magic.

import os, sys, argparse, struct

parser = argparse.ArgumentParser(prog='igm-test-patcher')
parser.add_argument('--rom', dest='rom', required=True, help='ROM path')
parser.add_argument('--payload', dest='payload', required=True, help='payload path (to the in game menu bin)')
parser.add_argument('--fontpack', dest='fontpack', required=True, help='FontPack file path')
parser.add_argument('--output', dest='out', required=True, help='Output ROM file')
args = parser.parse_args()

HOTKEY = 0x00F7    # L+R+START

# t_igmenu field offsets (see src/ingame.h / src/ingame.S).
# startup_insts[8]=0, tramp1=32, tramp2=48, menu_rsize=64,
# drv_issdhc=68, drv_rca=72, menu_hotkey=76, menu_lang=80,
# menu_use_directsave=84, menu_font_base=88, menu_cheats_base=92,
# scratch_space_base=96, scratch_space_size=100, has_rtc=104,
# menu_anim_speed=108, menu_palette[8]=112, savefile_backups=128.
OFF_HOTKEY   = 76
OFF_LANG     = 80
OFF_USEDS    = 84
OFF_FONTBASE = 88
OFF_CHEATS   = 92
OFF_PALETTE  = 112

def RGB2GBA(c):
  return ((c & 0xF80000) >> 19) | ((c & 0x00F800) >>  6) | ((c & 0x0000F8) <<  7)

rom = open(args.rom, "rb").read()
pload = bytearray(open(args.payload, "rb").read())
fpack = open(args.fontpack, "rb").read()

# Pad font pack (should be multiple of 4 already ...)
while len(fpack) % 4:
  fpack += b'\x00'

# Extract the entry point from the ROM by reading the first instruction
assert rom[0x3] == 0xEA  # It's always an unconditional branch!
start_addr = (struct.unpack("<I", rom[0:4])[0] & 0xFFFFFF) * 4 + 8 + 0x08000000

# The payload stub jumps to the ROM header entry-point field, so fill it
# with the game's real entry point.
rom = bytearray(rom)
rom[0xB8:0xBC] = struct.pack("<I", start_addr)
rom = bytes(rom)

# Replace inst with a branch to the payload
pload_entry = len(rom) + len(fpack)
start_inst = struct.pack("<I", 0xEA000000 | ((pload_entry >> 2) - 2))

# We place the menu right after the ROM, font pack first.
fpack_addr = 0x08000000 + len(rom)

# Patch the payload header fields at their current t_igmenu offsets.
def w32(off, v):
  pload[off:off+4] = struct.pack("<I", v)

def w16(off, v):
  pload[off:off+2] = struct.pack("<H", v)

w32(OFF_HOTKEY, HOTKEY)       # hotkey combo
w32(OFF_LANG, 0)              # lang: English
w32(OFF_USEDS, 0)             # no DirectSave
w32(OFF_FONTBASE, fpack_addr) # font pack address
w32(OFF_CHEATS, 0)            # cheat base addr
w16(OFF_PALETTE + 0, RGB2GBA(0xeca551))  # menu palette
w16(OFF_PALETTE + 2, RGB2GBA(0xbda27b))
w16(OFF_PALETTE + 4, RGB2GBA(0x000000))

# Pack it all!
outrom = start_inst + rom[4:] + fpack + bytes(pload)

# Padd it to power of two?
# TODO

open(args.out, "wb").write(outrom)
