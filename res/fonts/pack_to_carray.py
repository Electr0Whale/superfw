"""Convert .pack binary to C uint32_t array for embedding."""
import struct, sys

data = open(sys.argv[1], 'rb').read()

print('/*')
print(' * Embedded font data for SuperFW.')
print(' * Generated from Fusion Pixel 12px monospaced zh_hans BDF.')
print(' *')
print(' * Blocks: ascii (variable-width), check/arrows/arrows2 (12px fixed).')
print(f' * {len(data)} bytes total.')
print(' */')
print()
print('#include <stdint.h>')
print()
print('const uint32_t font_ascii_embedded[] = {')

for i in range(0, len(data), 16):
    chunk = data[i:i+16]
    if len(chunk) < 16:
        chunk = chunk + b'\x00' * (16 - len(chunk))
    vals = struct.unpack('<IIII', chunk)
    print(f'  0x{vals[0]:08x}, 0x{vals[1]:08x}, 0x{vals[2]:08x}, 0x{vals[3]:08x},')

print('};')
