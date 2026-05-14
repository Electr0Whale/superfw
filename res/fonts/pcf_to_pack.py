#!/usr/bin/env python3
"""PCF to SuperFW .pack font converter.

Parses PCF (Portable Compiled Format) bitmap fonts and converts to
the SuperFW binary font database format.

Usage:
  python3 pcf_to_pack.py --input wenquanyi_9pt.pcf --output ../fonts.pack
"""
import struct, sys, argparse

SPACE_PIXELS = 4

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
    "shapes":     (0x25A0, 0x26FF, "cjk"),
    "cjk-sym":    (0x3000, 0x303F, "cjk"),
    "cjk-compat": (0x3100, 0x4DFF, "cjk"),
    "hiragana":   (0x3040, 0x309F, "cjk"),
    "katakana":   (0x30A0, 0x30FF, "cjk"),
    "hangul":     (0xAC00, 0xD7A3, "cjk"),
    "fullwidth":  (0xFF00, 0xFFEF, "cjk"),
    "cjk-uni":    (0x4E00, 0x9FEF, "cjk"),
}

parser = argparse.ArgumentParser(prog='pcf_to_pack')
parser.add_argument('--input', required=True, help='PCF font file')
parser.add_argument('--output', required=True, help='Output .pack file')
parser.add_argument('--font-blocks', type=str, default=None,
                    help='Comma-separated blocks (default: all)')
parser.add_argument('--no-merge', action='store_true', help='Do not merge consecutive blocks')
args = parser.parse_args()


def parse_pcf(filepath):
    """Parse a PCF font, return {codepoint: {'columns': [uint16,...], 'bbx_w': w, 'bbx_h': h}}."""
    with open(filepath, 'rb') as f:
        data = f.read()

    # --- TOC ---
    magic = struct.unpack_from('<I', data, 0)[0]
    assert magic == 0x70636601, f"Bad PCF magic: {hex(magic)}"
    n_tables = struct.unpack_from('<I', data, 4)[0]
    toc = {}
    for i in range(n_tables):
        off = 8 + i * 16
        ttype, fmt, size, toff = struct.unpack_from('<IIII', data, off)
        toc[ttype] = (fmt, size, toff)

    # --- Encodings: codepoint -> glyph index ---
    efmt, esize, eoff = toc[32]
    enc_start = eoff + 16  # format(4) + 6*uint16(12)
    def cp_to_gidx(cp):
        return struct.unpack_from('>h', data, enc_start + cp * 2)[0]

    # --- Metrics: per-glyph dimensions ---
    mfmt, msize, moff = toc[4]
    nmetrics = (msize - 4) // 5
    is_compressed = (mfmt & 0x100) != 0
    assert is_compressed, "Only compressed metrics supported"
    mdata = data[moff + 4:]  # skip format word

    metrics_cache = {}
    def get_metrics(gidx):
        if gidx in metrics_cache:
            return metrics_cache[gidx]
        entry = mdata[gidx*5 : gidx*5+5]
        # This PCF uses non-standard field order: W, RSB, LSB, ascent, descent
        raw = [b - 0x80 for b in entry]
        char_w = raw[0]
        rsb = raw[1]
        lsb = raw[2]
        ascent = raw[3]
        descent = raw[4]
        result = {
            'width': char_w,
            'lsb': lsb, 'rsb': rsb,
            'ascent': ascent, 'descent': descent,
            'height': ascent + descent,
        }
        metrics_cache[gidx] = result
        return result

    # --- Bitmaps: per-glyph pixel data ---
    bfmt, bsize, boff = toc[8]
    big_endian = (bfmt & 0x02) != 0
    endian = '>' if big_endian else '<'
    nbitmaps = struct.unpack_from(endian + 'I', data, boff + 4)[0]
    offs_start = boff + 8
    bmp_offsets = []
    for i in range(nbitmaps):
        val = struct.unpack_from(endian + 'I', data, offs_start + i * 4)[0]
        bmp_offsets.append(val)
    bmp_data_start = offs_start + nbitmaps * 4
    bmp_data_size = bsize - 8 - nbitmaps * 4  # remaining bytes in table

    # --- Build glyph dict ---
    glyphs = {}
    print(f"  Parsing {nmetrics} glyphs...", end='', flush=True)
    for cp in range(0x0000, 0xFFFF):
        gidx = cp_to_gidx(cp)
        if gidx < 0 or gidx >= nmetrics:
            continue
        m = get_metrics(gidx)
        w_px = m['width']
        h_px = m['height']
        if w_px <= 0 or h_px <= 0:
            continue

        # Clip height to 16 rows (max for uint16 column storage)
        if h_px > 16:
            h_px = 16

        # Read bitmap
        gstart = bmp_data_start + bmp_offsets[gidx]
        gend = bmp_data_start + (bmp_offsets[gidx + 1] if gidx + 1 < nbitmaps else bmp_data_size)
        gbytes = gend - gstart
        bytes_per_row = (w_px + 7) // 8
        if bytes_per_row * h_px > gbytes:
            # Try smaller height
            h_px = gbytes // bytes_per_row
            if h_px <= 0:
                continue

        gdata = data[gstart:gend]

        # Convert row-major (1bpp, MSB-first) to column-major (uint16 per col)
        columns = [0] * w_px
        for row in range(min(h_px, 16)):
            row_start = row * bytes_per_row
            row_bytes = gdata[row_start : row_start + bytes_per_row]
            row_val = int.from_bytes(row_bytes, 'big')
            for col in range(w_px):
                bit_pos = (bytes_per_row * 8) - 1 - col
                if row_val & (1 << bit_pos):
                    columns[col] |= (1 << row)

        # Vertical shift: move glyph down by 1px for centering in 16px cell
        columns = [c << 1 for c in columns]

        glyphs[cp] = {
            'columns': columns,
            'bbx_w': w_px,
            'bbx_h': h_px,
        }
    print(f" got {len(glyphs)} glyphs")
    return glyphs


def trim_columns(columns):
    while columns and columns[0] == 0:
        columns = columns[1:]
    while columns and columns[-1] == 0:
        columns = columns[:-1]
    return columns


def dedup_and_index(glyphs, start_cp, end_cp):
    seen = {}
    pool = []
    index = []
    for cp in range(start_cp, end_cp + 1):
        if cp not in glyphs:
            index.append((-1, -1))
            continue
        cols = trim_columns(glyphs[cp]['columns'])
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
            enc = ((colsw - 1) << 12) | (offsets[data_idx])
            encoded.append(enc)

    result = b''.join(struct.pack('<H', x) for x in encoded) + pool_bytes
    if len(result) % 4:
        result += b'\x00' * (4 - (len(result) % 4))
    return result


def build_fixed_block(glyphs, start_cp, end_cp, col_count):
    data = b''
    for cp in range(start_cp, end_cp + 1):
        if cp not in glyphs:
            cols = [0x0000] * col_count
        else:
            cs = glyphs[cp]['columns']
            if len(cs) > col_count:
                cs = cs[:col_count]
            elif len(cs) < col_count:
                cs = cs + [0x0000] * (col_count - len(cs))
            cols = cs
        data += b''.join(struct.pack('<H', x) for x in cols)
    return data


def generate_pack(glyphs, block_names, output_path):
    blocks_to_build = []
    for name in block_names:
        if name in ALL_BLOCKS:
            blocks_to_build.append((name,) + ALL_BLOCKS[name])

    merged = []
    for name, bs, be, cat in blocks_to_build:
        if not args.no_merge and merged and merged[-1][3] == cat and merged[-1][2] + 1 == bs:
            merged[-1] = (merged[-1][0] + '+' + name, merged[-1][1], be, cat)
        else:
            merged.append((name, bs, be, cat))

    oblock_data = []
    oblock_meta = []

    for name, bs, be, cat in merged:
        count = be - bs + 1
        existing = sum(1 for cp in range(bs, be+1) if cp in glyphs)
        print(f"  Block {name}: U+{bs:04X}-U+{be:04X} ({cat}), {existing}/{count} glyphs")

        if existing == 0:
            print(f"    Skipping (no glyphs in font)")
            continue

        # Scan for max glyph width to pick strategy
        max_w = 0
        for cp in range(bs, be + 1):
            if cp in glyphs:
                w = glyphs[cp]['bbx_w']
                if w > max_w:
                    max_w = w

        if max_w <= 12:
            data = dedup_and_index(glyphs, bs, be)
            flags = 0
        elif max_w <= 12:
            data = build_fixed_block(glyphs, bs, be, 12)
            flags = 0x00000002  # FLAG_FW12
        else:
            data = build_fixed_block(glyphs, bs, be, 16)
            flags = 0x00000001  # FLAG_FW16
        oblock_data.append(data)
        oblock_meta.append((bs, be, flags))

    num_blocks = len(oblock_data)
    header_size = 8 + 16 * num_blocks
    offsets = []
    off = 0
    for d in oblock_data:
        offsets.append(off)
        off += len(d)
    total_size = header_size + off

    with open(output_path, 'wb') as f:
        f.write(struct.pack('<ccBBI', b'F', b'O', 1, num_blocks, total_size))
        for (bs, be, flags), doff in zip(oblock_meta, offsets):
            f.write(struct.pack('<IIII', bs, be, flags, doff))
        for d in oblock_data:
            f.write(d)

    print(f"  Wrote {output_path}: {total_size} bytes, {num_blocks} blocks")


def main():
    print(f"Loading PCF: {args.input}")
    glyphs = parse_pcf(args.input)

    block_names = [b.strip() for b in args.font_blocks.split(',')] if args.font_blocks else list(ALL_BLOCKS.keys())
    print(f"Generating .pack with blocks: {', '.join(block_names)}")
    generate_pack(glyphs, block_names, args.output)


if __name__ == '__main__':
    main()
