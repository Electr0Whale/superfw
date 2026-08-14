#!/usr/bin/env python3
"""Format tests for tools/coverart-pack.py.

Builds fixture BMP trees in a temp directory and exercises the generator +
pack format: empty pack, single entry, 65-entry page crossing, first/middle/
last lookups, missing game codes, duplicate codes, invalid BMPs, deterministic
output and atomic replacement. Run:

    python3 tools/test_coverart_pack.py
"""

import os
import importlib.util
import struct
import subprocess
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('coverart_pack',
                                               os.path.join(TOOLS, 'coverart-pack.py'))
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

FAILURES = []


def check(name, cond, extra=''):
    if cond:
        print('ok   %s' % name)
    else:
        print('FAIL %s %s' % (name, extra))
        FAILURES.append(name)


def make_bmp(path, w, h, color, spread=0):
    """Write a w x h 16bpp BMP filled with `color` (X1R5G5B5), optionally
    perturbed by a deterministic ramp so quantization produces real boxes."""
    rowbytes = (w * 2 + 3) & ~3
    pixels = bytearray()
    for y in range(h):
        for x in range(w):
            v = color
            if spread:
                v = (color + (x * 7 + y * 13) * spread) & 0x7FFF
            pixels += struct.pack('<H', v)
        pixels += b'\x00' * (rowbytes - w * 2)
    dataoff = 54
    hdr = b'BM' + struct.pack('<IHHI', 54 + len(pixels), 0, 0, dataoff)
    hdr += struct.pack('<IIIHHIIIIII', 40, w, h, 1, 16, 0,
                       len(pixels), 0, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(hdr + bytes(pixels))


def add_cover(root, code, w=120, h=75, color=0x7C00, spread=1):
    d = os.path.join(root, 'IMGS', code[0], code[1])
    os.makedirs(d, exist_ok=True)
    make_bmp(os.path.join(d, code + '.bmp'), w, h, color, spread)


def find_entry(pack, key):
    """Mirror the firmware's lookup: directory -> page -> binary search."""
    n = struct.unpack('<I', pack[12:16])[0]
    pages = struct.unpack('<I', pack[20:24])[0]
    idxoff = struct.unpack('<I', pack[28:32])[0]
    if pages == 0:
        return None
    lo, hi = 0, pages
    while lo < hi:
        mid = (lo + hi) >> 1
        if struct.unpack('<I', pack[512 + mid * 4:512 + mid * 4 + 4])[0] <= key:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    page = lo - 1
    base = page * 64
    cnt = min(64, n - base)
    lo, hi = 0, cnt
    while lo < hi:
        mid = (lo + hi) >> 1
        e = idxoff + (base + mid) * 8
        if struct.unpack('<I', pack[e:e + 4])[0] < key:
            lo = mid + 1
        else:
            hi = mid
    e = idxoff + (base + lo) * 8
    if lo < cnt and struct.unpack('<I', pack[e:e + 4])[0] == key:
        return struct.unpack('<I', pack[e + 4:e + 8])[0]
    return None


def run_pack(root, *extra):
    return subprocess.run([sys.executable, os.path.join(os.path.dirname(
        os.path.abspath(__file__)), 'coverart-pack.py'), root] + list(extra),
        capture_output=True, text=True)


def read_pack(root):
    with open(os.path.join(root, '.superfw', 'covers.pak'), 'rb') as f:
        return f.read()


def main():
    tmp = tempfile.mkdtemp(prefix='cvpk-')
    try:
        # --- empty pack ---------------------------------------------------
        root = os.path.join(tmp, 'empty')
        os.makedirs(os.path.join(root, 'IMGS'))
        r = run_pack(root)
        check('empty pack exit 0', r.returncode == 0, r.stderr)
        pack = read_pack(root)
        check('empty pack valid', cp.validate_pack(pack) == [])
        check('empty pack no entries', struct.unpack('<I', pack[12:16])[0] == 0)

        # --- single entry -------------------------------------------------
        root = os.path.join(tmp, 'single')
        add_cover(root, 'AWRE')
        r = run_pack(root)
        check('single exit 0', r.returncode == 0, r.stderr)
        pack = read_pack(root)
        check('single valid', cp.validate_pack(pack) == [])
        key = cp.gcode_key('AWRE')
        check('single entries', struct.unpack('<I', pack[12:16])[0] == 1)
        off = find_entry(pack, key)
        check('single found', off is not None)
        check('single aligned', off % 512 == 0)
        check('single dir', struct.unpack('<I', pack[512:516])[0] == key)
        check('missing code absent', find_entry(pack, cp.gcode_key('ZZZZ')) is None)

        # --- 65 entries (crosses one index page) --------------------------
        root = os.path.join(tmp, 'p65')
        codes = ['%c%c%02d' % (chr(ord('A') + i // 26), chr(ord('A') + i % 26), i % 100)
                 for i in range(65)]
        for c in codes:
            add_cover(root, c)
        r = run_pack(root)
        check('65 exit 0', r.returncode == 0, r.stderr)
        pack = read_pack(root)
        check('65 valid', cp.validate_pack(pack) == [])
        check('65 entries', struct.unpack('<I', pack[12:16])[0] == 65)
        check('65 pages', struct.unpack('<I', pack[20:24])[0] == 2)
        sorted_keys = sorted(cp.gcode_key(c) for c in codes)
        # first / middle / last lookups
        for label, k in (('first', sorted_keys[0]), ('middle', sorted_keys[32]),
                         ('last', sorted_keys[64])):
            off = find_entry(pack, k)
            check('65 %s found' % label, off is not None)
            check('65 %s aligned' % label, off % 512 == 0)
        # the cover at the found offset must carry the right key
        off = find_entry(pack, sorted_keys[32])
        check('65 middle key match',
              pack[off:off + 4] == b'CVR2' and
              struct.unpack('<I', pack[off + 4:off + 8])[0] == sorted_keys[32])
        check('65 dims', struct.unpack('<HH', pack[off + 8:off + 12]) == (120, 75))
        check('65 pal count', struct.unpack('<H', pack[off + 16:off + 18])[0] == 216)
        check('65 pixel bytes',
              struct.unpack('<I', pack[off + 12:off + 16])[0] == 120 * 75)
        # the directory's second entry = the first key of page 2
        check('65 dir page2', struct.unpack('<I', pack[516:520])[0] == sorted_keys[64])

        # --- duplicate game codes ----------------------------------------
        # The Windows FS cannot hold two files differing only in case, so
        # exercise the duplicate check in-process with a patched scanner.
        root = os.path.join(tmp, 'dup')
        os.makedirs(os.path.join(root, 'IMGS'))
        make_bmp(os.path.join(root, 'a.bmp'), 120, 75, 0x001F)
        make_bmp(os.path.join(root, 'b.bmp'), 120, 75, 0x7C00)
        orig_scan = cp.scan_cards
        cp.scan_cards = lambda _r: iter([
            (os.path.join(root, 'a.bmp'), 'AAAA'),
            (os.path.join(root, 'b.bmp'), 'AAAA')])
        sys.argv = ['coverart-pack.py', root]
        rc = cp.main()
        cp.scan_cards = orig_scan
        check('dup fails', rc != 0)
        check('dup no pack', not os.path.exists(
            os.path.join(root, '.superfw', 'covers.pak')))

        # --- invalid BMP --------------------------------------------------
        root = os.path.join(tmp, 'bad')
        d = os.path.join(root, 'IMGS', 'B', 'B')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'BBBB.bmp'), 'wb') as f:
            f.write(b'not a bitmap')
        r = run_pack(root)
        check('bad bmp fails', r.returncode != 0)
        # an over-size BMP must also abort the run
        d = os.path.join(root, 'IMGS', 'C', 'C')
        os.makedirs(d, exist_ok=True)
        make_bmp(os.path.join(d, 'CCCC.bmp'), 200, 75, 0x7C00)
        r = run_pack(root)
        check('oversize fails', r.returncode != 0)

        # --- deterministic output + atomic replace -----------------------
        root = os.path.join(tmp, 'det')
        for c in ('DET1', 'DET2', 'DET3'):
            add_cover(root, c)
        r = run_pack(root)
        pack1 = read_pack(root)
        r = run_pack(root)
        pack2 = read_pack(root)
        check('deterministic', pack1 == pack2)
        check('no tmp leftover', not os.path.exists(
            os.path.join(root, '.superfw', 'covers.pak.tmp')))
        # grow the set; the replace must atomically swap in the new pack
        add_cover(root, 'DET4')
        r = run_pack(root)
        pack3 = read_pack(root)
        check('replace updated', pack3 != pack1 and
              struct.unpack('<I', pack3[12:16])[0] == 4)

        # --- pixel payload sanity -----------------------------------------
        key = cp.gcode_key('DET4')
        off = find_entry(pack3, key)
        pixoff = off + 512
        pbytes = struct.unpack('<I', pack3[off + 12:off + 16])[0]
        pixels = pack3[pixoff:pixoff + pbytes]
        check('pixels in range', all(20 <= b <= 235 for b in pixels))
        check('pixels padded zero',
              all(b == 0 for b in pack3[pixoff + pbytes:pixoff + ((pbytes + 511) & ~511)]))
    finally:
        pass  # keep the temp dir for debugging; the OS cleans it up

    if FAILURES:
        print('\n%d failure(s): %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('\nall format tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
