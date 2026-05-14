#!/usr/bin/env python3
"""Combine two BDF fonts: primary font as base, secondary fills missing glyphs.

Usage:
  python3 combine_fonts.py \
    --primary wenquanyi_9pt.bdf \
    --secondary fusion-pixel-12px-monospaced-zh_hans.bdf \
    --output ../fonts.pack \
    --embed-output /tmp/embed.pack
"""
import struct, sys, subprocess, os, tempfile, argparse

ALL_BLOCKS = {
    "ascii":      (     0,   0x7F, "latin"),
    "latin":      (  0x80,   0xFF, "latin"),
    "latin-a":    ( 0x100,  0x17F, "latin"),
    "latin-b":    ( 0x180,  0x24F, "latin"),
    "gen-punct":  (0x2000, 0x206F, "latin"),
    "greek":      ( 0x370,  0x3FF, "latin"),
    "cyrilic":    ( 0x400,  0x4FF, "latin"),
    "check":      (0x2610, 0x2611, "cjk"),
    "triangles":  (0x25B2, 0x25C0, "cjk"),
    "cjk-sym":    (0x3000, 0x3009, "cjk"),
    "hiragana":   (0x3040, 0x309F, "cjk"),
    "katakana":   (0x30A0, 0x30FF, "cjk"),
    "hangul":     (0xAC00, 0xD7A3, "cjk"),
    "cjk-uni":    (0x4E00, 0x9FEF, "cjk"),
}

parser = argparse.ArgumentParser(prog='combine_fonts')
parser.add_argument('--primary', required=True, help='Primary BDF font (base style)')
parser.add_argument('--secondary', required=True, help='Secondary BDF font (fills gaps)')
parser.add_argument('--output', required=True)
parser.add_argument('--embed-output', help='Output .pack for embedded font')
args = parser.parse_args()

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SPACE_PIXELS = 4
BDF_CONV = os.path.join(THIS_DIR, 'bdf_to_pack.py')


def parse_glyphs_from_pack(filepath):
    """Parse .pack into {codepoint: (flags, width, columns_list)}."""
    with open(filepath, 'rb') as f:
        data = f.read()
    nblocks = data[3]
    glyphs = {}
    base = 8 + nblocks * 16
    for i in range(nblocks):
        off = 8 + i * 16
        start, end, flags, block_off = struct.unpack_from('<IIII', data, off)
        next_off = struct.unpack_from('<I', data, 8 + (i+1)*16 + 12)[0] if i+1 < nblocks else (len(data) - base)
        raw = data[base + block_off : base + next_off]
        nchars = end - start + 1

        if flags == 0:
            index = struct.unpack_from(f'<{nchars}H', raw, 0)
            pool_start = nchars * 2
            for j, cp in enumerate(range(start, end + 1)):
                ientry = index[j]
                if ientry != 0xFFFF:
                    w = (ientry >> 12) + 1
                    d_off = ientry & 0xFFF
                    cb = raw[pool_start + d_off*2 : pool_start + d_off*2 + w*2]
                    glyphs[cp] = (0, w, list(struct.unpack(f'<{w}H', cb)))
        elif flags & 0x02:
            for j, cp in enumerate(range(start, end + 1)):
                cb = raw[j*24 : j*24+24]
                cs = list(struct.unpack('<12H', cb))
                if any(c != 0 for c in cs):
                    glyphs[cp] = (0x02, 12, cs)
        elif flags & 0x01:
            for j, cp in enumerate(range(start, end + 1)):
                cb = raw[j*32 : j*32+32]
                cs = list(struct.unpack('<16H', cb))
                if any(c != 0 for c in cs):
                    glyphs[cp] = (0x01, 16, cs)
    return glyphs


def trim_columns(columns):
    while columns and columns[0] == 0:
        columns = columns[1:]
    while columns and columns[-1] == 0:
        columns = columns[:-1]
    return columns


def build_var_block(primary_g, secondary_g, start_cp, end_cp):
    """Variable-width block with per-glyph fill-in."""
    seen = {}
    pool = []
    index = []
    for cp in range(start_cp, end_cp + 1):
        entry = primary_g.get(cp) or secondary_g.get(cp)
        if entry is None:
            index.append((-1, -1))
            continue
        _, _, cols = entry
        cols = trim_columns(cols)
        if len(cols) == 0:
            cols = [0] * SPACE_PIXELS
        colsw = len(cols)
        if colsw > 12:
            cols = cols[:12]
            colsw = 12
        data = b''.join(struct.pack('<H', x) for x in cols)
        if data in seen:
            index.append((colsw, seen[data]))
        else:
            seen[data] = len(pool)
            pool.append(data)
            index.append((colsw, len(pool) - 1))

    pool_bytes = b''
    offsets = []
    for d in pool:
        offsets.append(len(pool_bytes) // 2)
        pool_bytes += d

    encoded = []
    for colsw, data_idx in index:
        if colsw < 0:
            encoded.append(0xFFFF)
        else:
            encoded.append(((colsw - 1) << 12) | offsets[data_idx])

    result = b''.join(struct.pack('<H', x) for x in encoded) + pool_bytes
    if len(result) % 4:
        result += b'\x00' * (4 - (len(result) % 4))
    return (0, result)


def build_fixed_block(primary_g, secondary_g, start_cp, end_cp, col_count):
    """Fixed-width block with per-glyph fill-in."""
    data = b''
    empty = [0x0000] * col_count
    for cp in range(start_cp, end_cp + 1):
        entry = primary_g.get(cp) or secondary_g.get(cp)
        if entry is None:
            cols = empty
        else:
            cs = entry[2]
            if len(cs) > col_count:
                cs = cs[:col_count]
            elif len(cs) < col_count:
                cs = cs + [0x0000] * (col_count - len(cs))
            cols = cs
        data += b''.join(struct.pack('<H', x) for x in cols)

    flags = 0x00000002 if col_count == 12 else 0x00000001
    return (flags, data)


def build_merged_block(primary_g, secondary_g, start_cp, end_cp, cat):
    """Determine block strategy and build."""
    max_w = 0
    for cp in range(start_cp, end_cp + 1):
        entry = primary_g.get(cp) or secondary_g.get(cp)
        if entry:
            w = entry[1]
            if w > max_w:
                max_w = w

    if max_w == 0:
        return None  # skip empty block

    if max_w <= 8:
        return build_var_block(primary_g, secondary_g, start_cp, end_cp)
    elif max_w <= 12:
        return build_fixed_block(primary_g, secondary_g, start_cp, end_cp, 12)
    else:
        return build_fixed_block(primary_g, secondary_g, start_cp, end_cp, 16)


# Step 1: Generate packs from both BDFs
all_blocks = ','.join(ALL_BLOCKS.keys())
tmpdir = tempfile.gettempdir()

print(f"=== Primary: {args.primary} ===")
pri_pack = os.path.join(tmpdir, 'combine_pri.pack')
subprocess.run([sys.executable, BDF_CONV,
    '--bdf', args.primary, '--font-blocks', all_blocks, '--no-merge',
    '--output', pri_pack], check=True)

print(f"\n=== Secondary: {args.secondary} ===")
sec_pack = os.path.join(tmpdir, 'combine_sec.pack')
subprocess.run([sys.executable, BDF_CONV,
    '--bdf', args.secondary, '--font-blocks', all_blocks, '--no-merge',
    '--output', sec_pack], check=True)

print("\n=== Parsing ===")
pri_glyphs = parse_glyphs_from_pack(pri_pack)
sec_glyphs = parse_glyphs_from_pack(sec_pack)
print(f"  Primary:   {len(pri_glyphs)} glyphs")
print(f"  Secondary: {len(sec_glyphs)} glyphs")

# Step 2: Build merged blocks
print("\n=== Merging (primary + secondary fill) ===")
oblock_data = []
oblock_meta = []

for name, (bs, be, cat) in ALL_BLOCKS.items():
    count = be - bs + 1
    c_pri = sum(1 for cp in range(bs, be+1) if cp in pri_glyphs)
    c_sec = sum(1 for cp in range(bs, be+1) if cp in sec_glyphs)
    c_both = sum(1 for cp in range(bs, be+1) if cp in pri_glyphs or cp in sec_glyphs)

    if c_both == 0:
        print(f"  {name:12s}: empty, skipping")
        continue

    filled = c_both - c_pri
    pct_pri = c_pri * 100 // count if count else 0
    pct_both = c_both * 100 // count if count else 0
    print(f"  {name:12s}: pri={c_pri}/{count} ({pct_pri}%) +{filled} -> {c_both}/{count} ({pct_both}%)")

    result = build_merged_block(pri_glyphs, sec_glyphs, bs, be, cat)
    if result is None:
        continue
    flags, data = result
    oblock_data.append(data)
    oblock_meta.append((bs, be, flags))

# Step 3: Write combined .pack
header_size = 8 + 16 * len(oblock_data)
offsets = []
off = 0
for d in oblock_data:
    offsets.append(off)
    off += len(d)
total_size = header_size + off

with open(args.output, 'wb') as f:
    f.write(struct.pack('<ccBBI', b'F', b'O', 1, len(oblock_data), total_size))
    for (bs, be, flags), doff in zip(oblock_meta, offsets):
        f.write(struct.pack('<IIII', bs, be, flags, doff))
    for d in oblock_data:
        f.write(d)

print(f"\n  Wrote {args.output}: {total_size} bytes, {len(oblock_data)} blocks")

# Step 4: Embedded font
if args.embed_output:
    print(f"\n=== Embedded font (primary + secondary) ===")
    # Generate with both fonts for max coverage
    emb_pri = os.path.join(tmpdir, 'emb_pri.pack')
    emb_sec = os.path.join(tmpdir, 'emb_sec.pack')
    subprocess.run([sys.executable, BDF_CONV,
        '--bdf', args.primary, '--font-blocks', 'ascii,check,triangles', '--no-merge',
        '--output', emb_pri], check=True)
    subprocess.run([sys.executable, BDF_CONV,
        '--bdf', args.secondary, '--font-blocks', 'ascii,check,triangles', '--no-merge',
        '--output', emb_sec], check=True)

    ep = parse_glyphs_from_pack(emb_pri)
    es = parse_glyphs_from_pack(emb_sec)

    odata = []
    ometa = []
    for name in ['ascii', 'check', 'triangles']:
        bs, be, cat = ALL_BLOCKS[name]
        c_both = sum(1 for cp in range(bs, be+1) if cp in ep or cp in es)
        if c_both == 0:
            continue
        result = build_merged_block(ep, es, bs, be, cat)
        if result:
            flags, data = result
            odata.append(data)
            ometa.append((bs, be, flags))

    hsize = 8 + 16 * len(odata)
    offs = []
    off = 0
    for d in odata:
        offs.append(off)
        off += len(d)

    with open(args.embed_output, 'wb') as f:
        f.write(struct.pack('<ccBBI', b'F', b'O', 1, len(odata), hsize + off))
        for (bs, be, flags), doff in zip(ometa, offs):
            f.write(struct.pack('<IIII', bs, be, flags, doff))
        for d in odata:
            f.write(d)
    print(f"  Wrote {args.embed_output}")

os.unlink(pri_pack)
os.unlink(sec_pack)
print("\nDone.")
