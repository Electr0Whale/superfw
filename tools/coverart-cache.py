#!/usr/bin/env python3
"""Offline cover-art cache generator for the SuperFW cover browser.

Generates /.superfw/imgcache/{CODE}.img for every 16bpp BMP under
/IMGS/{c0}/{c1}/{CODE}.bmp on a SuperFW SD card, using the exact same
median-cut quantization as the firmware (src/coverart.c). With the cache
present the GBA skips the histogram + median-cut + remap entirely and just
reads the cached pixels + palette.

Cache layout (little-endian, see src/coverart.c):
    u32 magic "CVR1"
    u16 width, u16 height
    u32 src_size, u16 src_date, u16 src_time   (source BMP FAT stat)
    u16 palette[216]                            (GBA BGR555)
    u8  pixels[width*height]                    (palette indices, biased by 20)

Usage:
    python3 tools/coverart-cache.py <card-root> [--force] [--limit N]

Existing valid caches are skipped (incremental); --force regenerates all.
"""

import os
import struct
import sys
import time

MAGIC = b'CVR1'
HDRSZ = 4 + 2 + 2 + 4 + 2 + 2 + 216 * 2
NBOXES = 215


def rd32(d, o):
    return d[o] | (d[o + 1] << 8) | (d[o + 2] << 16) | (d[o + 3] << 24)


def rd16(d, o):
    return d[o] | (d[o + 1] << 8)


def dos_date_time(ts):
    """Pack a mtime into the FAT fdate/ftime format (FatFs FILINFO)."""
    t = time.localtime(ts)
    date = ((max(t.tm_year, 1980) - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday
    ftime = (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)
    return date & 0xFFFF, ftime & 0xFFFF


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
    """Return (width, height, palette list, pixel bytes) for a 16bpp BMP."""
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


def cache_valid(cpath, fsize, fdate, ftime, width, height):
    try:
        with open(cpath, 'rb') as f:
            h = f.read(HDRSZ)
    except OSError:
        return False
    if len(h) != HDRSZ or h[:4] != MAGIC:
        return False
    if struct.unpack('<H', h[4:6])[0] != width or struct.unpack('<H', h[6:8])[0] != height:
        return False
    if struct.unpack('<I', h[8:12])[0] != fsize:
        return False
    if struct.unpack('<H', h[12:14])[0] != fdate or struct.unpack('<H', h[14:16])[0] != ftime:
        return False
    return True


def write_cache(cpath, width, height, fsize, fdate, ftime, pal, pix):
    hdr = MAGIC
    hdr += struct.pack('<HHIHH', width, height, fsize, fdate, ftime)
    hdr += struct.pack('<216H', *pal)
    tmp = cpath + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(hdr)
        f.write(pix)
    os.replace(tmp, cpath)


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
    force = '--force' in sys.argv
    limit = None
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])
    if not args:
        print(__doc__)
        return 2
    root = args[0]

    cache_dir = os.path.join(root, '.superfw', 'imgcache')
    os.makedirs(cache_dir, exist_ok=True)

    done = skipped = failed = 0
    t0 = time.time()
    for bmp, code in scan_cards(root):
        if limit is not None and done + skipped >= limit:
            break
        st = os.stat(bmp)
        fdate, ftime = dos_date_time(st.st_mtime)
        cpath = os.path.join(cache_dir, code + '.img')
        try:
            q = quantize(bmp)
        except Exception as e:
            failed += 1
            print('FAIL %s: %s' % (code, e))
            continue
        if q is None:
            failed += 1
            print('SKIP %s: unsupported BMP (not 16bpp or >136x75)' % code)
            continue
        width, height, pal, pix = q
        if not force and cache_valid(cpath, st.st_size, fdate, ftime, width, height):
            skipped += 1
            continue
        write_cache(cpath, width, height, st.st_size, fdate, ftime, pal, pix)
        done += 1
        if done % 250 == 0:
            print('... %d generated, %d skipped' % (done, skipped))

    print('done: %d generated, %d skipped (up to date), %d failed in %.1fs' %
          (done, skipped, failed, time.time() - t0))
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
