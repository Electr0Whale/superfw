#!/usr/bin/env python3
"""Build a complete SuperFW GBA cover pack from libretro Named_Boxarts.

Only titles present in GBHWDB's GBA cartridge table are accepted. This filters
e-reader cards, trading cards, logos and other unrelated artwork. The source
archive contains Windows-safe names (``&`` is commonly replaced by ``_``) and
some text files that point at another PNG; both forms are handled here.
"""

import argparse
import hashlib
import json
import re
import struct
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageFile

# Some libretro PNGs have a truncated ancillary chunk but a complete pixel
# stream. Pillow can decode those safely for this read-only conversion pass.
ImageFile.LOAD_TRUNCATED_IMAGES = True

import importlib.util


GBHWDB_URL = "https://gbhwdb.gekkio.fi/cartridges/gba.html"
MAX_W = 136
HEIGHT = 75
ROM_RE = re.compile(r"^AGB-([A-Za-z0-9]{4})-[0-9]+$")
INVALID_WIN = re.compile(r'[<>:"/\\|?*&]')


def load_pack_module():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("coverart_pack", here / "coverart-pack.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def norm(s):
    return " ".join(s.casefold().strip().split())


def source_name(title):
    # The libretro archive replaces characters that cannot be used in a
    # Windows filename. Keep spaces and punctuation otherwise unchanged.
    return INVALID_WIN.sub("_", title).rstrip(" .")


def gbhwdb_rows():
    req = urllib.request.Request(GBHWDB_URL, headers={"User-Agent": "superfw-cover-pack/1"})
    with urllib.request.urlopen(req, timeout=30) as response:
        html = response.read()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("GBHWDB page has no cartridge table")
    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        title = cells[0].get_text(" ", strip=True)
        rom_id = cells[1].get_text(" ", strip=True)
        m = ROM_RE.match(rom_id)
        if m:
            rows.append((title, m.group(1).upper()))
    if not rows:
        raise RuntimeError("GBHWDB page yielded no GBA ROM IDs")
    return rows


def resolve_png(path, seen=None):
    """Resolve real PNGs and the archive's one-line filename aliases."""
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen:
        raise RuntimeError("cyclic PNG alias: %s" % path.name)
    seen.add(path)
    data = path.read_bytes()
    try:
        with Image.open(path):
            # A few archive entries have JPEG data despite the .png suffix.
            # The decoder, rather than the extension, is authoritative.
            return path
    except Exception:
        pass
    if len(data) < 4096:
        try:
            target = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            target = ""
        candidate = path.parent / target
        if target and candidate.suffix.casefold() == ".png" and candidate.is_file():
            return resolve_png(candidate, seen)
    raise RuntimeError("not a PNG or resolvable alias: %s" % path.name)


def write_bmp16(path, image):
    """Write a top-down X1R5G5B5 BMP for the existing quantizer."""
    w, h = image.size
    rowbytes = (w * 2 + 3) & ~3
    pixels = bytearray(rowbytes * h)
    rgb = image.convert("RGB")
    for y in range(h):
        for x in range(w):
            r, g, b = rgb.getpixel((x, y))
            r5 = (r * 31 + 127) // 255
            g5 = (g * 31 + 127) // 255
            b5 = (b * 31 + 127) // 255
            struct.pack_into("<H", pixels, y * rowbytes + x * 2,
                             (r5 << 10) | (g5 << 5) | b5)
    file_size = 54 + len(pixels)
    dib = struct.pack("<IIIHHIIIIII", 40, w, -h & 0xFFFFFFFF, 1, 16, 0,
                      len(pixels), 0, 0, 0, 0)
    path.write_bytes(b"BM" + struct.pack("<IHHI", file_size, 0, 0, 54) + dib + pixels)


def resize_cover(path):
    with Image.open(path) as original:
        im = original.convert("RGBA")
        if im.getbbox() is None:
            raise RuntimeError("empty image: %s" % path.name)
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg.convert("RGB")
        width = max(1, round(im.width * HEIGHT / im.height))
        im = im.resize((width, HEIGHT), Image.Resampling.LANCZOS)
        if width > MAX_W:
            left = (width - MAX_W) // 2
            im = im.crop((left, 0, left + MAX_W, HEIGHT))
        return im


def load_alias_map(path, source, code_files):
    """Add ROM-code aliases and explicitly named source covers from JSON."""
    if path is None:
        return 0, 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("cannot read alias map %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise SystemExit("alias map must be a JSON object")
    aliases = data.get("aliases", {})
    direct = data.get("direct", {})
    if not isinstance(aliases, dict) or not isinstance(direct, dict):
        raise SystemExit("alias map aliases and direct entries must be objects")

    def code(value, context):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{4}", value):
            raise SystemExit("invalid ROM code for %s: %r" % (context, value))
        return value

    for dest, source_code in sorted(aliases.items()):
        dest = code(dest, "alias destination")
        source_code = code(source_code, "alias source")
        if dest in code_files:
            raise SystemExit("alias destination already has a cover: %s" % dest)
        if source_code not in code_files:
            raise SystemExit("alias source has no matched cover: %s" % source_code)
        code_files[dest] = code_files[source_code]

    for dest, filename in sorted(direct.items()):
        dest = code(dest, "direct destination")
        if dest in code_files:
            raise SystemExit("direct destination already has a cover: %s" % dest)
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise SystemExit("invalid direct source filename for %s" % dest)
        candidate = source / filename
        if not candidate.is_file():
            raise SystemExit("direct source not found for %s: %s" % (dest, candidate))
        try:
            code_files[dest] = resolve_png(candidate)
        except RuntimeError as exc:
            raise SystemExit("invalid direct source for %s: %s" % (dest, exc))
    return len(aliases), len(direct)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path, help="Named_Boxarts directory")
    ap.add_argument("output", type=Path, help="output covers.pak")
    ap.add_argument("--alias-map", type=Path,
                    help="JSON aliases/direct covers to add after GBHWDB matching")
    args = ap.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_dir():
        raise SystemExit("source directory not found: %s" % source)

    rows = gbhwdb_rows()
    by_title = {}
    for title, code in rows:
        by_title.setdefault(norm(title), set()).add(code)
        by_title.setdefault(norm(source_name(title)), set()).add(code)

    files = {}
    for path in sorted(source.glob("*.png"), key=lambda p: p.name.casefold()):
        files.setdefault(norm(path.stem), []).append(path)

    matched_titles = {}
    code_files = {}
    unmatched = []
    for key, codes in by_title.items():
        candidates = files.get(key, [])
        if not candidates:
            continue
        title_file = candidates[0]
        try:
            real = resolve_png(title_file)
            # Validate now so a broken matching image cannot silently reduce
            # the supposedly complete pack.
            with Image.open(real) as im:
                im.verify()
        except Exception as exc:
            raise SystemExit("matched image is invalid (%s): %s" % (title_file.name, exc))
        matched_titles[key] = real
        for code in codes:
            code_files.setdefault(code, real)

    known_keys = set(by_title)
    for path in sorted(source.glob("*.png")):
        if norm(path.stem) not in known_keys:
            unmatched.append(path.name)

    if not code_files:
        raise SystemExit("no GBHWDB titles matched Named_Boxarts")
    alias_count, direct_count = load_alias_map(
        args.alias_map.resolve() if args.alias_map else None, source, code_files)
    all_codes = {code for _title, code in rows}
    missing_codes = sorted(all_codes - set(code_files))

    cp = load_pack_module()
    items = []
    with tempfile.TemporaryDirectory(prefix="gba-boxart-") as td:
        temp = Path(td)
        for code, real in sorted(code_files.items()):
            try:
                image = resize_cover(real)
                bmp = temp / (code + ".bmp")
                write_bmp16(bmp, image)
                q = cp.quantize(str(bmp))
                if q is None:
                    raise RuntimeError("quantizer rejected %s" % image.size)
                items.append((code,) + q)
            except Exception as exc:
                raise SystemExit("failed %s from %s: %s" % (code, real.name, exc))
        pack = cp.build_pack(items)
        errors = cp.validate_pack(pack)
        if errors:
            raise SystemExit("pack validation failed: " + "; ".join(errors))
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_name(output.name + ".tmp")
        tmp.write_bytes(pack)
        tmp.replace(output)

    report = output.with_suffix(output.suffix + ".report.txt")
    report.write_text(
        "GBHWDB: %s\nsource PNG files: %d\nmatched title keys: %d\n"
        "GBHWDB ROM rows: %d\nGBHWDB unique ROM codes: %d\n"
        "unique ROM codes: %d\nalias covers: %d\ndirect covers: %d\n"
        "missing source covers: %d\n"
        "unmatched source files: %d\n"
        "pack bytes: %d\nsha256: %s\n" % (
            GBHWDB_URL, len(list(source.glob("*.png"))), len(matched_titles),
            len(rows), len(all_codes), len(items), alias_count, direct_count,
            len(missing_codes),
            len(unmatched), len(pack), hashlib.sha256(pack).hexdigest()),
        encoding="utf-8")
    if missing_codes:
        with report.open("a", encoding="utf-8") as f:
            f.write("missing ROM IDs: %s\n" % ", ".join(missing_codes))
    print("wrote %s: %d ROM codes, %d bytes" % (output, len(items), len(pack)))
    if alias_count or direct_count:
        print("added %d aliases and %d direct covers" % (alias_count, direct_count))
    print("matched title keys: %d; excluded source images: %d" %
          (len(matched_titles), len(unmatched)))
    print("report: %s" % report)


if __name__ == "__main__":
    main()
