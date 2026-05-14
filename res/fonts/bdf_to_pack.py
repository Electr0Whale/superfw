#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BDF to SuperFW .pack font converter.

Converts Fusion Pixel Font BDF files (12px monospaced) into the SuperFW
binary font database format.

The BDF font has two character widths:
  - Latin/Cyrillic/Greek: 6px wide, 12px tall (DWIDTH=6, BBX 6 12)
  - CJK/Hangul/Kana:      12px wide, 12px tall (DWIDTH=12, BBX 12 12)

In the .pack output:
  - Latin blocks use variable-width mode (flags=0), stores 1-6 columns/char
  - CJK blocks use FLAG_FW12 (flags=0x02), stores 12 columns/char
Each column is a uint16_t with pixel data in bits 0..11 (top-aligned),
bits 12..15 always zero.

Usage:
  python3 bdf_to_pack.py --bdf fusion-pixel-12px-monospaced-zh_hans.bdf \
      --output ../fonts.pack
"""

import os, sys, argparse, struct

SPACE_PIXELS = 4    # Fallback width for empty glyphs (space)

parser = argparse.ArgumentParser(prog='bdf_to_pack')
parser.add_argument('--bdf', dest='bdffile', required=True, help='BDF font file')
parser.add_argument('--output', dest='out', required=True, help='Output .pack file')
parser.add_argument('--debug-png', dest='dbgpng', type=str, default=None, help='Debug font PNG file')
parser.add_argument('--no-merge', action='store_true', help='Do not merge consecutive blocks')
parser.add_argument('--font-blocks', dest='blocks', type=str, default=None,
                    help='Comma-separated list of blocks to include (default: all)')
args = parser.parse_args()


# Block definitions: (start_cp, end_cp, category)
# category: "latin" (6px variable) or "cjk" (12px fixed)
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
    "arrows":     (0x2BC5, 0x2BC8, "cjk"),
    "arrows2":    (0x25B8, 0x25B8, "cjk"),
    "cjk-sym":    (0x3000, 0x303F, "cjk"),
    "cjk-compat": (0x3100, 0x4DFF, "cjk"),
    "hiragana":   (0x3040, 0x309F, "cjk"),
    "katakana":   (0x30A0, 0x30FF, "cjk"),
    "hangul":     (0xAC00, 0xD7A3, "cjk"),
    "fullwidth":  (0xFF00, 0xFFEF, "cjk"),
    "cjk-uni":    (0x4E00, 0x9FEF, "cjk"),
    "fixwidth":   (0xFF01, 0xFF20, "cjk"),
}


def parse_bdf(filepath):
    """Parse a BDF font file.

    Returns (glyphs, max_descent) where:
      glyphs: {codepoint: {'bbx_w','bbx_h','bbx_yoff','columns'}}
      max_descent: global max pixels below baseline (abs of most-negative yoff)
    Each column is a uint16 with baseline-aligned pixel data in bits 0..15.
    """
    glyphs = {}
    current_cp = None
    current_dwidth = None
    current_bbx = None
    current_bitmap = []
    in_bitmap = False
    total_chars = 0
    global_max_yoff = -99  # most-negative yoff seen (descent)

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()

            if line.startswith('FONTBOUNDINGBOX '):
                parts = line.split()
                global_max_yoff = int(parts[4])  # yoff, typically negative

            elif line.startswith('CHARS '):
                total_chars = int(line.split()[1])
                print(f"  Parsing {total_chars} characters...", end='', flush=True)

            elif line.startswith('STARTCHAR '):
                current_cp = None
                current_dwidth = None
                current_bbx = None
                current_bitmap = []
                in_bitmap = False

            elif line.startswith('ENCODING '):
                current_cp = int(line.split()[1])

            elif line.startswith('DWIDTH '):
                parts = line.split()
                current_dwidth = int(parts[1])

            elif line.startswith('BBX '):
                parts = line.split()
                current_bbx = (int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]))

            elif line == 'BITMAP':
                in_bitmap = True

            elif line == 'ENDCHAR':
                in_bitmap = False
                if current_cp is not None and current_cp >= 0 and current_bbx:
                    bbx_w, bbx_h, bbx_xoff, bbx_yoff = current_bbx
                    # Convert row-major bitmap to column-major
                    columns = [0] * bbx_w
                    for row_idx, hex_row in enumerate(current_bitmap):
                        if row_idx >= bbx_h:
                            break
                        row_val = int(hex_row, 16)
                        total_bits = len(hex_row) * 4
                        for col in range(bbx_w):
                            bit_pos = total_bits - 1 - col
                            if row_val & (1 << bit_pos):
                                columns[col] |= (1 << row_idx)
                    glyphs[current_cp] = {
                        'dwidth': current_dwidth or bbx_w,
                        'bbx_w': bbx_w,
                        'bbx_h': bbx_h,
                        'bbx_yoff': bbx_yoff,
                        'columns': columns,
                    }

            elif in_bitmap:
                current_bitmap.append(line)

    # Apply baseline-aligned vertical shift
    if global_max_yoff == -99:
        global_max_yoff = -4  # sane default

    max_descent = abs(global_max_yoff)
    global_baseline = 15 - max_descent  # baseline row: 2px above + CJK + 2px below

    print(f" got {len(glyphs)} glyphs (descent={max_descent}, baseline_row={global_baseline})")

    for cp, g in glyphs.items():
        h = g['bbx_h']
        yoff = g['bbx_yoff']
        # Baseline row within the glyph's own bitmap (0 = top, h-1 = bottom)
        glyph_baseline = h + yoff - 1  # yoff is negative → baseline above bottom
        shift = global_baseline - glyph_baseline
        if shift < 0:
            shift = 0
        if shift + h > 16:
            shift = 16 - h
        if shift < 0:
            shift = 0
        g['columns'] = [c << shift for c in g['columns']]
        g['bbx_h'] = h  # unchanged

    return glyphs, max_descent


def trim_columns(columns):
    """Remove leading and trailing zero columns. Returns trimmed list."""
    while columns and columns[0] == 0:
        columns = columns[1:]
    while columns and columns[-1] == 0:
        columns = columns[:-1]
    return columns


def dedup_and_index(glyphs_in_range, start_cp, end_cp):
    """Build variable-width (Latin) block data with deduplication.

    Returns: (index_entries, column_data_bytes, total_unique)
      index_entries: list of uint16 (one per codepoint in range)
      column_data_bytes: concatenated column data
    """
    unique_data = {}   # bytes -> offset in pool
    column_pool = []   # list of bytes entries
    index = []

    for cp in range(start_cp, end_cp + 1):
        if cp not in glyphs_in_range:
            index.append((-1, -1))   # Missing char
            continue

        g = glyphs_in_range[cp]
        cols = g['columns']
        cols = trim_columns(cols)

        if len(cols) == 0:
            cols = [0] * SPACE_PIXELS

        colsw = len(cols)
        if colsw > 12:
            cols = cols[:12]
            colsw = 12
        if colsw == 0:
            colsw = 1
            cols = [0]

        # Pack columns as uint16 little-endian
        data = b''.join(struct.pack('<H', x) for x in cols)

        if data in unique_data:
            index.append((colsw, unique_data[data]))
        else:
            offset = len(unique_data)
            unique_data[data] = offset
            column_pool.append(data)
            index.append((colsw, offset))

    # Build offset table into concatenated pool
    pool_bytes = b''
    offsets = []
    for data in column_pool:
        offsets.append(len(pool_bytes) // 2)  # offset in uint16 units
        pool_bytes += data

    # Encode index: top 3 bits = (width - 1), bottom 13 bits = offset
    encoded = []
    for colsw, data_idx in index:
        if colsw < 0:
            encoded.append(0xFFFF)
        else:
            assert data_idx < 4096, f"Too many unique glyphs: {data_idx}"
            enc = ((colsw - 1) << 12) | (offsets[data_idx])
            encoded.append(enc)

    # Pack index + pool
    index_bytes = b''.join(struct.pack('<H', x) for x in encoded)
    result = index_bytes + pool_bytes
    # Pad to 4-byte alignment
    if len(result) % 4:
        result += b'\x00' * (4 - (len(result) % 4))
    return result


def build_fixed_block(glyphs_in_range, start_cp, end_cp, col_count):
    """Build fixed-width (CJK) block data.

    Each glyph: col_count * uint16_t (col_count * 2 bytes).
    Narrower glyphs are left-aligned (zero-padded on right).
    """
    data = b''
    for cp in range(start_cp, end_cp + 1):
        if cp not in glyphs_in_range:
            cols = [0x0000] * col_count
        else:
            colsource = glyphs_in_range[cp]['columns']
            if len(colsource) > col_count:
                # Truncate if somehow wider (shouldn't happen)
                cols = colsource[:col_count]
            elif len(colsource) < col_count:
                # Left-align, zero-pad on right
                cols = colsource + [0x0000] * (col_count - len(colsource))
            else:
                cols = colsource
        data += b''.join(struct.pack('<H', x) for x in cols)
    return data


def generate_pack(glyphs, block_names, output_path):
    """Generate the .pack binary file."""

    # Determine block list
    picked = set(block_names)
    blocks_to_build = []
    for name in picked:
        if name in ALL_BLOCKS:
            blocks_to_build.append((name,) + ALL_BLOCKS[name])
        else:
            print(f"  Warning: unknown block '{name}', skipping")

    # Merge consecutive blocks of same category
    merged = []
    for name, bs, be, cat in blocks_to_build:
        if not args.no_merge and merged and merged[-1][3] == cat and merged[-1][2] + 1 == bs:
            merged[-1] = (merged[-1][0] + '+' + name, merged[-1][1], be, cat)
        else:
            merged.append((name, bs, be, cat))

    # Build data for each block
    oblock_data = []
    oblock_meta = []  # (start, end, flags)

    for name, bs, be, cat in merged:
        count = be - bs + 1
        print(f"  Block {name}: U+{bs:04X}-U+{be:04X} ({cat}), {count} chars")

        if cat == "latin":
            # Scan max glyph width in block; if > 8, use fixed-width
            max_w = 0
            for cp in range(bs, be + 1):
                if cp in glyphs:
                    w = len(trim_columns(glyphs[cp]['columns']))
                    if w > max_w:
                        max_w = w
            if max_w <= 12:
                data = dedup_and_index(glyphs, bs, be)
                flags = 0  # variable-width
            else:
                data = build_fixed_block(glyphs, bs, be, 16)
                flags = 0x00000001  # FLAG_FW16
            existing = sum(1 for cp in range(bs, be+1) if cp in glyphs)
            print(f"    {existing} glyphs present, max_w={max_w}, {len(data)} bytes")
        else:
            data = build_fixed_block(glyphs, bs, be, 12)
            flags = 0x00000002  # FLAG_FW12
            existing = sum(1 for cp in range(bs, be+1) if cp in glyphs)
            print(f"    {existing} glyphs present, {len(data)} bytes")

        oblock_data.append(data)
        oblock_meta.append((bs, be, flags))

    # Write .pack file
    num_blocks = len(oblock_data)
    # Calculate offsets
    header_size = 8 + 16 * num_blocks
    offsets = []
    off = 0
    for d in oblock_data:
        offsets.append(off)
        off += len(d)
    total_size = header_size + off

    with open(output_path, 'wb') as f:
        # Header
        f.write(struct.pack('<ccBBI', b'F', b'O', 1, num_blocks, total_size))
        # Block index
        for (bs, be, flags), data_off in zip(oblock_meta, offsets):
            f.write(struct.pack('<IIII', bs, be, flags, data_off))
        # Block data
        for d in oblock_data:
            f.write(d)

    print(f"  Wrote {output_path}: {total_size} bytes, {num_blocks} blocks")


def generate_debug_png(glyphs, output_path):
    """Generate a debug PNG showing all glyphs in a grid (16px cells)."""
    try:
        from PIL import Image
    except ImportError:
        print("  PIL not available, skipping debug PNG")
        return

    all_cps = sorted(glyphs.keys())
    ncols = 64
    nrows = (len(all_cps) + ncols - 1) // ncols

    cell_w, cell_h = 16, 16
    im = Image.new('RGB', (ncols * cell_w, nrows * cell_h), (255, 255, 255))

    for idx, cp in enumerate(all_cps):
        g = glyphs[cp]
        cols = g['columns']
        col_count = len(cols)
        row_count = g['bbx_h']

        cx = (idx % ncols) * cell_w
        cy = (idx // ncols) * cell_h

        for ci, col_val in enumerate(cols):
            px_x = cx + ci
            for ri in range(row_count):
                if col_val & (1 << ri):
                    px_y = cy + ri
                    if 0 <= px_x < im.width and 0 <= px_y < im.height:
                        im.putpixel((px_x, px_y), (0, 0, 0))

    im.save(output_path)
    print(f"  Debug PNG saved to {output_path}")


def main():
    print(f"Loading BDF: {args.bdffile}")
    glyphs, max_descent = parse_bdf(args.bdffile)

    # Determine which blocks to generate
    if args.blocks:
        block_names = [b.strip() for b in args.blocks.split(',')]
    else:
        # Default: generate all blocks present in ALL_BLOCKS
        block_names = list(ALL_BLOCKS.keys())

    print(f"Generating .pack with blocks: {', '.join(block_names)}")
    generate_pack(glyphs, block_names, args.out)

    if args.dbgpng:
        generate_debug_png(glyphs, args.dbgpng)


if __name__ == '__main__':
    main()
