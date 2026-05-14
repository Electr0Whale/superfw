"""Debug CJK glyph rendering from PCF."""
import struct

d = open('wenquanyi_9pt.pcf', 'rb').read()

toc = {}
for i in range(struct.unpack_from('<I', d, 4)[0]):
    t = struct.unpack_from('<IIII', d, 8 + i*16)
    toc[t[0]] = (t[1], t[2], t[3])

# Encodings
ef, es, eo = toc[32]
enc_start = eo + 16

def cp2g(cp):
    return struct.unpack_from('>h', d, enc_start + cp*2)[0]

# Metrics (field order: W, RSB, LSB, A, D)
mf, ms, mo = toc[4]
md = d[mo+4:]

# Bitmaps (BE)
bf, bs, bo = toc[8]
endian = '>'
nbmps = struct.unpack_from(endian + 'I', d, bo+4)[0]
offs_start = bo + 8
bmp_offs = [struct.unpack_from(endian + 'I', d, offs_start + i*4)[0] for i in range(nbmps)]
bmp_data_off = offs_start + nbmps * 4
bmp_data_size = bs - 8 - nbmps * 4

# Debug U+4E00 (一) and U+4E2D (中)
for cp in [0x4E00, 0x4E2D, 0x6587, 0x5B57]:
    gidx = cp2g(cp)
    entry = md[gidx*5:gidx*5+5]
    raw = [b-0x80 for b in entry]
    w, rsb, lsb, asc, desc = raw
    h = asc + desc
    if h > 16:
        h = 16

    gstart = bmp_data_off + (bmp_offs[gidx] if gidx >= 0 else 0)
    gend = bmp_data_off + (bmp_offs[gidx+1] if gidx+1 < nbmps else bmp_data_size)
    gsize = gend - gstart
    bpr = (w + 7) // 8
    actual_h = min(gsize // bpr, 16)

    print(f'\nU+{cp:04X} gidx={gidx}: W={w} RSB={rsb} LSB={lsb} A={asc} D={desc} h={h}')
    print(f'  bitmap: {gsize}B bpr={bpr} actual_h={actual_h}')

    # Render as row-major
    print(f'  Row-major (first {min(actual_h,16)} rows):')
    gd = d[gstart:gend]
    for row in range(min(actual_h, 16)):
        rv = int.from_bytes(gd[row*bpr:(row+1)*bpr], 'big')
        bits = ''.join('#' if (rv >> (bpr*8-1-i)) & 1 else '.' for i in range(w))
        print(f'    {bits}')

    # Convert to column-major (SuperFW format) and render back
    columns = [0] * w
    for row in range(min(actual_h, 16)):
        rv = int.from_bytes(gd[row*bpr:(row+1)*bpr], 'big')
        for col in range(w):
            if (rv >> (bpr*8-1-col)) & 1:
                columns[col] |= (1 << row)
    # Shift down 1px
    columns = [c << 1 for c in columns]

    print(f'  Column-major (re-rendered):')
    for row in range(16):
        bits = ''.join('#' if (columns[col] >> row) & 1 else '.' for col in range(w))
        if '#' in bits:
            print(f'    {bits}')

    # Check if columns look valid (not all zeros, not all ones)
    non_zero = sum(1 for c in columns if c != 0)
    print(f'  Non-zero columns: {non_zero}/{w}')
