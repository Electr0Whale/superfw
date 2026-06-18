# SuperFW Font Build

The standard font build path for this fork is:

```bash
python3 res/fonts/build.py
```

It generates:

- `res/fonts.pack`
- `res/fonts-ext.pack`
- `src/fonts/font_embed.h`

Font sources:

- Primary: `wenquanyi_10pt.bdf`
- Fallback: `fusion-pixel-12px-monospaced-zh_hans.bdf`

The legacy upstream generator is still kept for reference because upstream uses
it, but this fork's committed font assets come from the BDF-based build above.
