# SuperFW Font Pack

## Fusion Pixel 12px Monospaced (current)

Source: https://github.com/TakWolf/fusion-pixel-font (MIT/OFL-1.1 license)

Download the BDF release from:
  https://github.com/TakWolf/fusion-pixel-font/releases/latest
  (fusion-pixel-font-12px-monospaced-bdf-vYYYY.MM.DD.zip)

Extract `fusion-pixel-12px-monospaced-zh_hans.bdf` and place it here.

### Standard pack (fonts.pack):
```
python3 bdf_to_pack.py \
  --bdf fusion-pixel-12px-monospaced-zh_hans.bdf \
  --font-blocks cjk-sym,latin,latin-a,latin-b,greek,cyrilic,hiragana,katakana,cjk-uni,hangul \
  --output ../fonts.pack
```

### Extended pack (fonts-ext.pack, for Chis board):
Same as standard; both use pre-composed hangul.

### Embedded font (font_embed.h):
```
python3 bdf_to_pack.py \
  --bdf fusion-pixel-12px-monospaced-zh_hans.bdf \
  --font-blocks ascii,check,triangles \
  --output /tmp/embed-fonts.pack

python3 pack_to_carray.py /tmp/embed-fonts.pack > ../../src/fonts/font_embed.h
```

## Legacy (UNSCII / Unifont)

The old generator.py and unscii-16.hex / hangul-blocks.hex files are kept for reference
but are no longer used in the build pipeline.
