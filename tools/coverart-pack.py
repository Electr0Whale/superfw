#!/usr/bin/env python3
"""Offline cover-art resource-pack generator for the SuperFW cover browser.

Builds the single packed cover file /.superfw/covers.pak from the
/IMGS/{c0}/{c1}/{CODE}.bmp tree on a SuperFW SD card, using the exact same
median-cut quantization the firmware used for the old per-file caches. The
firmware then reads covers exclusively from the pack: page directory +
paged index + CVR2 cover blocks, so browsing is one binary search + one
multi-sector read instead of dozens of scattered file operations.

Pack layout (little-endian, see docs/coverart-pack-acceleration-plan.md and
src/coverart.c):

    sector 0   : "CVPK" global header (512 bytes)
    sectors 1+ : page directory, first game-code key of each index page
                 (page_count x u32, zero-padded to 512 bytes)
    then       : index pages, 512 bytes each, 64 entries of
                 { u32 gamecode_key, u32 cover_offset }
    then       : cover blocks, 512-byte aligned, each =
                   "CVR2" header sector (magic, key, w/h, pixel bytes,
                   palette count 216, format 1, u16 palette[216], zero pad)
                 + packed pixels (width*height bytes, palette indices
                   biased by 20) zero-padded to the sector boundary

Game codes are sorted by c0<<24 | c1<<16 | c2<<8 | c3. Duplicate game codes,
invalid dimensions or unsupported BMPs abort the run with a non-zero exit
status and no partial output. The finished pack is re-parsed and validated
before it atomically replaces the previous file.

Usage:
    python3 tools/coverart-pack.py <card-root> [--limit N]
"""

import os
import struct
import sys
import time

MAGIC_PACK = b'CVPK'
MAGIC_COVER = b'CVR2'
VERSION = 1
HDRSZ = 512
ENTRIES_PER_PAGE = 64
ENTRY_SZ = 8
MAX_PAGES = 512
MAX_ENTRIES = MAX_PAGES * ENTRIES_PER_PAGE
NBOXES = 215
NPAL = 216


def rd32(d, o):
    return d[o] | (d[o + 1] << 8) | (d[o + 2] << 16) | (d[o + 3] << 24)


def rd16(d, o):
    return d[o] | (d[o + 1] << 8)


def align512(n):
    return (n + 511) & ~511


def median_cut(hist, nboxes=NBOXES):
    # (r0, r1, g0, g1, b0, b1, count), ranges inclusive on the 4-bit grid
    boxes = [[0, 15, 0, 15, 0, 15, 0]]

    def cnt(b):
        c = 0
        for r in range(b[0], b[1] + 1):
            for g in range(b[2], b[3] + 1):
                for bb in range(b[4], b[5] + 1):
                    c += hist[(r << 8) | (g << 4) | bb]
        return c

    boxes[0][6] = cnt(boxes[0])
    while len(boxes) < nboxes:
        best, bestlen = -1, 0
        for i, b in enumerate(boxes):
            ln = max(b[1] - b[0], b[3] - b[2], b[5] - b[4])
            if ln and (best < 0 or b[6] > boxes[best][6]):
                best, bestlen = i, ln
        if best < 0 or not bestlen:
            break
        b = boxes[best]
        ranges = [(b[0], b[1]), (b[2], b[3]), (b[4], b[5])]
        ax = max(range(3), key=lambda k: ranges[k][1] - ranges[k][0])
        a0, a1 = ranges[ax]
        half = (b[6] + 1) // 2
        acc, splitv = 0, a0
        for v in range(a0, a1):
            sl = 0
            for r in range(b[0], b[1] + 1):
                for g in range(b[2], b[3] + 1):
                    for bb in range(b[4], b[5] + 1):
                        if (r, g, bb)[ax] == v:
                            sl += hist[(r << 8) | (g << 4) | bb]
            acc += sl
            if acc >= half:
                splitv = v
                break
        hi = b[:]
        lo_i, hi_i = (0, 1) if ax == 0 else ((2, 3) if ax == 1 else (4, 5))
        b[hi_i] = splitv
        hi[lo_i] = splitv + 1
        oldcount = b[6]
        b[6] = cnt(b)
        hi[6] = oldcount - b[6]
        boxes.append(hi)

    lut = [0] * 4096
    for i, b in enumerate(boxes):
        for r in range(b[0], b[1] + 1):
            for g in range(b[2], b[3] + 1):
                for bb in range(b[4], b[5] + 1):
                    lut[(r << 8) | (g << 4) | bb] = i + 1
    return lut


def quantize(path):
    """Return (width, height, palette list, pixel bytes) for a 16bpp BMP.

    Returns None when the file is not a supported 16bpp BMP within
    136x75; the caller treats that as a hard error (no partial packs).
    """
    with open(path, 'rb') as f:
        d = f.read()
    if d[:2] != b'BM' or len(d) < 54:
        return None
    width = struct.unpack('<i', d[18:22])[0]
    rawh = struct.unpack('<i', d[22:26])[0]
    bpp = struct.unpack('<H', d[28:30])[0]
    dataoff = struct.unpack('<I', d[10:14])[0]
    topdown = rawh < 0
    height = -rawh if topdown else rawh
    if bpp != 16 or width <= 0 or height <= 0 or width > 136 or height > 75:
        return None
    rowbytes = (width * 2 + 3) & ~3

    hist = [0] * 4096
    for sy in range(height):
        base = dataoff + sy * rowbytes
        for x in range(width):
            v = rd16(d, base + x * 2)
            i = (((v >> 11) & 0xF) << 8) | (((v >> 6) & 0xF) << 4) | ((v >> 1) & 0xF)
            if hist[i] != 0xFF:
                hist[i] += 1

    lut = median_cut(hist)
    sums = [[0, 0, 0, 0] for _ in range(NBOXES)]  # r, g, b, cnt
    pix = bytearray()
    for sy in range(height):
        base = dataoff + sy * rowbytes
        for x in range(width):
            v = rd16(d, base + x * 2)
            r, g, b = (v >> 10) & 0x1F, (v >> 5) & 0x1F, v & 0x1F
            box = lut[(r >> 1) << 8 | (g >> 1) << 4 | (b >> 1)]
            pix.append(20 + box)
            if box:
                s = sums[box - 1]
                s[0] += r
                s[1] += g
                s[2] += b
                s[3] += 1

    pal = [0] * 216
    for i, s in enumerate(sums):
        if s[3]:
            pal[i + 1] = (((s[2] + s[3] // 2) // s[3]) << 10) | \
                         (((s[1] + s[3] // 2) // s[3]) << 5) | \
                         ((s[0] + s[3] // 2) // s[3])
    return width, height, pal, bytes(pix)


def gcode_key(code):
    b = code.encode('ascii')
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


def build_pack(items):
    """items = [(code, width, height, pal, pix)], sorted by key.

    Returns the complete pack as bytes."""
    n = len(items)
    page_count = (n + ENTRIES_PER_PAGE - 1) // ENTRIES_PER_PAGE
    dir_size = align512(page_count * 4)
    index_offset = HDRSZ + dir_size
    data_offset = index_offset + page_count * HDRSZ

    hdr = bytearray(HDRSZ)
    hdr[0:4] = MAGIC_PACK
    struct.pack_into('<I', hdr, 4, VERSION)
    struct.pack_into('<I', hdr, 8, HDRSZ)
    struct.pack_into('<I', hdr, 12, n)
    struct.pack_into('<I', hdr, 16, ENTRIES_PER_PAGE)
    struct.pack_into('<I', hdr, 20, page_count)
    struct.pack_into('<I', hdr, 24, HDRSZ)          # page_directory_offset
    struct.pack_into('<I', hdr, 28, index_offset)
    struct.pack_into('<I', hdr, 32, data_offset)

    dird = bytearray(dir_size)
    for p in range(page_count):
        struct.pack_into('<I', dird, p * 4, gcode_key(items[p * ENTRIES_PER_PAGE][0]))

    idx = bytearray(page_count * HDRSZ)
    for i, (code, w, h, pal, pix) in enumerate(items):
        struct.pack_into('<II', idx, i * ENTRY_SZ, gcode_key(code), 0)

    data = bytearray()
    for i, (code, w, h, pal, pix) in enumerate(items):
        off = data_offset + len(data)
        struct.pack_into('<I', idx, i * ENTRY_SZ + 4, off)
        blk = bytearray(HDRSZ)
        blk[0:4] = MAGIC_COVER
        struct.pack_into('<I', blk, 4, gcode_key(code))
        struct.pack_into('<HH', blk, 8, w, h)
        struct.pack_into('<I', blk, 12, w * h)
        struct.pack_into('<HH', blk, 16, NPAL, 1)
        struct.pack_into('<%dH' % NPAL, blk, 20, *pal)
        data += blk
        data += pix
        data += b'\x00' * (align512(len(pix)) - len(pix))

    struct.pack_into('<I', hdr, 36, data_offset + len(data))
    return bytes(hdr) + bytes(dird) + bytes(idx) + bytes(data)


def validate_pack(d):
    """Re-parse the finished pack and verify every offset, size and entry.

    Returns a list of error strings (empty = valid)."""
    errs = []
    if len(d) < HDRSZ:
        return ['file shorter than the header']
    if d[0:4] != MAGIC_PACK:
        errs.append('bad global magic')
    ver = struct.unpack('<I', d[4:8])[0]
    if ver != VERSION:
        errs.append('bad version %d' % ver)
    if struct.unpack('<I', d[8:12])[0] != HDRSZ:
        errs.append('bad header size')
    n = struct.unpack('<I', d[12:16])[0]
    epp = struct.unpack('<I', d[16:20])[0]
    pages = struct.unpack('<I', d[20:24])[0]
    diroff = struct.unpack('<I', d[24:28])[0]
    idxoff = struct.unpack('<I', d[28:32])[0]
    dataoff = struct.unpack('<I', d[32:36])[0]
    total = struct.unpack('<I', d[36:40])[0]
    if n > MAX_ENTRIES:
        errs.append('entry count %d exceeds the limit' % n)
    if epp != ENTRIES_PER_PAGE:
        errs.append('bad entries per page')
    if pages > MAX_PAGES:
        errs.append('page count %d exceeds the limit' % pages)
    if pages != (n + ENTRIES_PER_PAGE - 1) // ENTRIES_PER_PAGE:
        errs.append('page count %d does not match %d entries' % (pages, n))
    if diroff != HDRSZ:
        errs.append('bad directory offset %d' % diroff)
    expidx = HDRSZ + align512(pages * 4)
    if idxoff != expidx:
        errs.append('index offset %d, expected %d' % (idxoff, expidx))
    if dataoff != expidx + pages * HDRSZ:
        errs.append('bad data offset %d' % dataoff)
    if total != len(d):
        errs.append('total size %d != file size %d' % (total, len(d)))
    if errs:
        return errs

    keys = []
    for i in range(n):
        key = struct.unpack('<I', d[idxoff + i * ENTRY_SZ:idxoff + i * ENTRY_SZ + 4])[0]
        off = struct.unpack('<I', d[idxoff + i * ENTRY_SZ + 4:idxoff + i * ENTRY_SZ + 8])[0]
        keys.append(key)
        if i == 0 or (i % ENTRIES_PER_PAGE) == 0:
            dirkey = struct.unpack('<I', d[diroff + (i // ENTRIES_PER_PAGE) * 4:
                                          diroff + (i // ENTRIES_PER_PAGE) * 4 + 4])[0]
            if dirkey != key:
                errs.append('directory entry %d: %08x != %08x' % (i // ENTRIES_PER_PAGE, dirkey, key))
        if off % HDRSZ:
            errs.append('entry %d: unaligned offset %d' % (i, off))
            continue
        if off < dataoff or off + HDRSZ > len(d):
            errs.append('entry %d: offset %d out of range' % (i, off))
            continue
        blk = d[off:off + HDRSZ]
        if blk[0:4] != MAGIC_COVER:
            errs.append('entry %d: bad cover magic' % i)
        if struct.unpack('<I', blk[4:8])[0] != key:
            errs.append('entry %d: key mismatch' % i)
        w, h = struct.unpack('<HH', blk[8:12])
        if not (0 < w <= 136 and 0 < h <= 75):
            errs.append('entry %d: bad size %dx%d' % (i, w, h))
        if struct.unpack('<I', blk[12:16])[0] != w * h:
            errs.append('entry %d: pixel bytes != w*h' % i)
        if struct.unpack('<H', blk[16:18])[0] != NPAL:
            errs.append('entry %d: bad palette count' % i)
        if struct.unpack('<H', blk[18:20])[0] != 1:
            errs.append('entry %d: bad pixel format' % i)
        if off + HDRSZ + align512(w * h) > len(d):
            errs.append('entry %d: pixels out of range' % i)
    for i in range(1, n):
        if keys[i] <= keys[i - 1]:
            errs.append('keys not strictly sorted at %d' % i)
    return errs


def scan_cards(root):
    """Yield (bmp_path, code) for every /IMGS/{c0}/{c1}/{CODE}.bmp."""
    imgs = os.path.join(root, 'IMGS')
    if not os.path.isdir(imgs):
        return
    for c0 in sorted(os.listdir(imgs)):
        d0 = os.path.join(imgs, c0)
        if not os.path.isdir(d0):
            continue
        for c1 in sorted(os.listdir(d0)):
            d1 = os.path.join(d0, c1)
            if not os.path.isdir(d1):
                continue
            for fn in sorted(os.listdir(d1)):
                if fn.lower().endswith('.bmp') and len(fn) == 8:
                    yield os.path.join(d1, fn), fn[:4].upper()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    limit = None
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])
    if not args:
        print(__doc__)
        return 2
    root = args[0]

    t0 = time.time()
    seen = {}
    items = []
    errors = []
    for bmp, code in scan_cards(root):
        if limit is not None and len(items) + len(errors) >= limit:
            break
        if code in seen:
            errors.append('duplicate game code %s (%s and %s)' % (code, seen[code], bmp))
            continue
        try:
            q = quantize(bmp)
        except Exception as e:
            errors.append('%s: %s' % (code, e))
            continue
        if q is None:
            errors.append('%s: unsupported BMP (not 16bpp or >136x75)' % code)
            continue
        seen[code] = bmp
        items.append((code,) + q)

    if errors:
        for e in errors:
            print('ERROR %s' % e)
        print('aborting: %d error(s), no pack written' % len(errors))
        return 1

    items.sort(key=lambda it: gcode_key(it[0]))
    if len(items) > MAX_ENTRIES:
        print('ERROR: %d covers exceed the %d-entry limit' % (len(items), MAX_ENTRIES))
        return 1
    pack = build_pack(items)

    verrs = validate_pack(pack)
    if verrs:
        for e in verrs:
            print('VALIDATE %s' % e)
        print('aborting: output failed self-validation, no pack written')
        return 1

    dest = os.path.join(root, '.superfw', 'covers.pak')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(pack)
    os.replace(tmp, dest)

    print('wrote %s: %d covers, %d index pages, %d bytes in %.1fs' %
          (dest, len(items), (len(items) + 63) // 64, len(pack), time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
