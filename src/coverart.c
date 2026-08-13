/*
 * Cover-art / title-screen preview for the ROM browser.  See coverart.h.
 *
 * Reads "/IMGS/{c0}/{c1}/{CODE}.bmp" (up to 136x75, 16bpp X1R5G5B5) directly off
 * the SD card, quantizes it into a per-image adaptive palette (median-cut over
 * a 4:4:4 histogram, refined to exact 5-bit means; MEM_PALETTE[20..235]) and
 * caches the resulting 8bpp image for fast per-frame blits.
 */
#include <string.h>
#include <stdbool.h>

#include "gbahw.h"
#include "fatfs/ff.h"
#include "common.h"
#include "nanoprintf.h"
#include "coverart.h"

#define COVER_DIR  "/IMGS"

// Big buffers go in EWRAM (.sbss); the default .bss lives in scarce IWRAM.
#define EWRAM_BSS  __attribute__((section(".sbss")))

static EWRAM_BSS __attribute__((aligned(4))) uint8_t cover_pix[COVER_MAX_W * COVER_H];
static EWRAM_BSS char cover_key[512];     // ROM path the current state belongs to
static bool     cover_have;               // a valid cover is loaded (.bss/IWRAM -> zeroed)
static uint16_t cover_ww;                 // width of the loaded cover
static uint16_t cube_pal[CUBE_NCOLORS];   // the color cube, tuned per image (GBA BGR555)

// Median-cut state. The histogram quantizes to 4 bits per channel (16x16x16 =
// 4096 bins, 4KB) to keep the split pass fast; the palette colors themselves
// are computed at full 5-bit precision from the pixel sums below.
#define COVER_QN          16
#define COVER_QBUF_SZ     (COVER_QN * COVER_QN * COVER_QN)
#define COVER_NBOXES      215        // palette slot 0 stays black (letterbox)

static EWRAM_BSS uint8_t cover_qbuf[COVER_QBUF_SZ];    // hist, reused as the bin->box LUT
static EWRAM_BSS uint32_t cover_rsum[COVER_NBOXES];    // per-box pixel sums (full 5-bit)
static EWRAM_BSS uint32_t cover_gsum[COVER_NBOXES];
static EWRAM_BSS uint32_t cover_bsum[COVER_NBOXES];
static EWRAM_BSS uint16_t cover_rcnt[COVER_NBOXES];
static EWRAM_BSS uint16_t cover_nboxes;

typedef struct {
  uint8_t r0, r1, g0, g1, b0, b1;   // inclusive 4-bit ranges
  uint16_t count;
} t_cover_box;
static EWRAM_BSS t_cover_box cover_boxes[COVER_NBOXES];

static inline unsigned qbin(unsigned r4, unsigned g4, unsigned b4) {
  return (r4 << 8) | (g4 << 4) | b4;
}

static inline uint8_t *qhist(unsigned r4, unsigned g4, unsigned b4) {
  return &cover_qbuf[qbin(r4, g4, b4)];
}

static uint16_t box_count(t_cover_box *b) {
  unsigned c = 0;
  for (unsigned r = b->r0; r <= b->r1; r++)
    for (unsigned g = b->g0; g <= b->g1; g++)
      for (unsigned bb = b->b0; bb <= b->b1; bb++)
        c += *qhist(r, g, bb);
  return c;
}

// Classic median-cut over the 4:4:4 histogram: repeatedly split the box with
// the most pixels along its longest axis at the count median. The result is a
// partition of the color cube whose boxes each hold ~equal pixel mass.
static void cover_cut(void) {
  cover_boxes[0] = (t_cover_box){ 0, COVER_QN - 1, 0, COVER_QN - 1, 0, COVER_QN - 1, 0 };
  cover_boxes[0].count = box_count(&cover_boxes[0]);
  cover_nboxes = 1;

  while (cover_nboxes < COVER_NBOXES) {
    // Pick the largest box that can still be split.
    int best = -1;
    unsigned bestlen = 0;
    for (unsigned i = 0; i < cover_nboxes; i++) {
      t_cover_box *b = &cover_boxes[i];
      unsigned len = b->r1 - b->r0;
      if (b->g1 - b->g0 > len) len = b->g1 - b->g0;
      if (b->b1 - b->b0 > len) len = b->b1 - b->b0;
      if (len && (best < 0 || b->count > cover_boxes[best].count)) {
        best = (int)i;
        bestlen = len;
      }
    }
    if (best < 0 || !bestlen)
      break;

    t_cover_box *b = &cover_boxes[best];
    unsigned ax = 0, a0 = b->r0, a1 = b->r1;
    if (b->g1 - b->g0 > a1 - a0) { ax = 1; a0 = b->g0; a1 = b->g1; }
    if (b->b1 - b->b0 > a1 - a0) { ax = 2; a0 = b->b0; a1 = b->b1; }

    // Find the median value along the axis (first value where the cumulative
    // count reaches half the box's mass).
    unsigned half = (b->count + 1) / 2, acc = 0, splitv = a0;
    for (unsigned v = a0; v < a1; v++) {
      unsigned slice = 0;
      if (ax == 0) {
        for (unsigned g = b->g0; g <= b->g1; g++)
          for (unsigned bb = b->b0; bb <= b->b1; bb++)
            slice += *qhist(v, g, bb);
      } else if (ax == 1) {
        for (unsigned r = b->r0; r <= b->r1; r++)
          for (unsigned bb = b->b0; bb <= b->b1; bb++)
            slice += *qhist(r, v, bb);
      } else {
        for (unsigned r = b->r0; r <= b->r1; r++)
          for (unsigned g = b->g0; g <= b->g1; g++)
            slice += *qhist(r, g, v);
      }
      acc += slice;
      if (acc >= half) {
        splitv = v;
        break;
      }
    }

    // Shrink this box to the lower half and append the upper half.
    t_cover_box hi = *b;
    if (ax == 0) {
      b->r1 = (uint8_t)splitv;
      hi.r0 = (uint8_t)(splitv + 1);
    } else if (ax == 1) {
      b->g1 = (uint8_t)splitv;
      hi.g0 = (uint8_t)(splitv + 1);
    } else {
      b->b1 = (uint8_t)splitv;
      hi.b0 = (uint8_t)(splitv + 1);
    }
    b->count = box_count(b);
    hi.count = box_count(&hi);
    cover_boxes[cover_nboxes++] = hi;
  }

  // Turn the histogram buffer into a bin -> box LUT (1-based; 0 = black).
  memset(cover_qbuf, 0, sizeof(cover_qbuf));
  for (unsigned i = 0; i < cover_nboxes; i++) {
    t_cover_box *b = &cover_boxes[i];
    for (unsigned r = b->r0; r <= b->r1; r++)
      for (unsigned g = b->g0; g <= b->g1; g++)
        for (unsigned bb = b->b0; bb <= b->b1; bb++)
          *qhist(r, g, bb) = (uint8_t)(i + 1);
  }
}

static bool gcode_is_alnum(const uint8_t *c) {
  for (unsigned i = 0; i < 4; i++) {
    uint8_t ch = c[i];
    if (!((ch >= '0' && ch <= '9') ||
          (ch >= 'A' && ch <= 'Z') ||
          (ch >= 'a' && ch <= 'z')))
      return false;
  }
  return true;
}

static bool load_cover_file(const uint8_t gcode[4]) {
  char path[64];
  npf_snprintf(path, sizeof(path), "%s/%c/%c/%c%c%c%c.bmp",
               COVER_DIR, gcode[0], gcode[1],
               gcode[0], gcode[1], gcode[2], gcode[3]);

  FIL fd;
  if (FR_OK != f_open(&fd, path, FA_READ))
    return false;

  bool ok = false;
  UINT rd;
  uint8_t hdr[54];

  if (FR_OK == f_read(&fd, hdr, sizeof(hdr), &rd) && rd == sizeof(hdr) &&
      hdr[0] == 'B' && hdr[1] == 'M') {
    uint32_t dataoff = hdr[10] | (hdr[11] << 8) | (hdr[12] << 16) | (hdr[13] << 24);
    int32_t  width   = hdr[18] | (hdr[19] << 8) | (hdr[20] << 16) | (hdr[21] << 24);
    int32_t  rawh    = hdr[22] | (hdr[23] << 8) | (hdr[24] << 16) | (hdr[25] << 24);
    unsigned bpp     = hdr[28] | (hdr[29] << 8);
    bool topdown = rawh < 0;
    int32_t height = topdown ? -rawh : rawh;

    if (bpp == 16 && width > 0 && width <= COVER_MAX_W &&
        height > 0 && height <= COVER_H && FR_OK == f_lseek(&fd, dataoff)) {
      unsigned rowbytes = ((unsigned)width * 2 + 3) & ~3u;   // 4-byte aligned rows
      uint8_t rowbuf[COVER_MAX_W * 2];

      // Pass 1: 4:4:4 histogram of the image.
      memset(cover_qbuf, 0, sizeof(cover_qbuf));
      ok = true;
      for (int sy = 0; sy < height; sy++) {
        if (FR_OK != f_read(&fd, rowbuf, rowbytes, &rd) || rd != rowbytes) {
          ok = false;
          break;
        }
        for (int x = 0; x < width; x++) {
          unsigned v = rowbuf[x * 2] | (rowbuf[x * 2 + 1] << 8);
          uint8_t *h = qhist((v >> 11) & 0xF, (v >> 6) & 0xF, (v >> 1) & 0xF);
          if (*h != 0xFF)
            (*h)++;
        }
      }

      if (ok) {
        cover_cut();

        // Pass 2: remap the pixels through the boxes, accumulating full
        // 5-bit sums so the palette means are exact.
        // Pad letterbox (smaller images) with palette index 0 (= black).
        memset(cover_pix, CUBE_PAL_BASE, sizeof(cover_pix));
        memset(cover_rsum, 0, sizeof(cover_rsum));
        memset(cover_gsum, 0, sizeof(cover_gsum));
        memset(cover_bsum, 0, sizeof(cover_bsum));
        memset(cover_rcnt, 0, sizeof(cover_rcnt));
        if (FR_OK != f_lseek(&fd, dataoff))
          ok = false;
        for (int sy = 0; ok && sy < height; sy++) {
          if (FR_OK != f_read(&fd, rowbuf, rowbytes, &rd) || rd != rowbytes) {
            ok = false;
            break;
          }
          unsigned dy = topdown ? (unsigned)sy : (unsigned)(height - 1 - sy);
          uint8_t *dst = &cover_pix[dy * COVER_MAX_W];
          for (int x = 0; x < width; x++) {
            unsigned v = rowbuf[x * 2] | (rowbuf[x * 2 + 1] << 8);
            unsigned r = (v >> 10) & 0x1F, g = (v >> 5) & 0x1F, b = v & 0x1F;
            uint8_t box = cover_qbuf[qbin(r >> 1, g >> 1, b >> 1)];
            dst[x] = CUBE_PAL_BASE + box;
            if (box) {
              box--;
              cover_rsum[box] += r;
              cover_gsum[box] += g;
              cover_bsum[box] += b;
              cover_rcnt[box]++;
            }
          }
        }
      }

      if (ok) {
        // Build the final palette from the exact per-box means.
        for (unsigned i = 0; i < cover_nboxes; i++) {
          unsigned cnt = cover_rcnt[i];
          if (cnt) {
            unsigned mr = (cover_rsum[i] + cnt / 2) / cnt;
            unsigned mg = (cover_gsum[i] + cnt / 2) / cnt;
            unsigned mb = (cover_bsum[i] + cnt / 2) / cnt;
            cube_pal[i + 1] = (mb << 10) | (mg << 5) | mr;
          } else {
            cube_pal[i + 1] = 0;
          }
        }
        cube_pal[0] = 0;   // letterbox stays black
        cover_ww = (uint16_t)width;
        dma_memcpy16(&MEM_PALETTE[CUBE_PAL_BASE], cube_pal, CUBE_NCOLORS);
      }
    }
  }

  f_close(&fd);
  return ok;
}

void coverart_invalidate(void) {
  cover_key[0] = 0;
  cover_have = false;
}

void coverart_update(const char *rom_fullpath, uint32_t filesize, bool is_gba) {
  // No-op while the selection hasn't moved (avoids re-reading the SD card).
  if (0 == strncmp(cover_key, rom_fullpath, sizeof(cover_key) - 1))
    return;

  strncpy(cover_key, rom_fullpath, sizeof(cover_key) - 1);
  cover_key[sizeof(cover_key) - 1] = 0;
  cover_have = false;

  if (!is_gba)
    return;

  t_rom_header romh;
  if (0 != preload_gba_rom(rom_fullpath, filesize, &romh))
    return;

  if (gcode_is_alnum(romh.gcode))
    cover_have = load_cover_file(romh.gcode);
}

void coverart_update_gcode(const char *cachekey, const uint8_t gcode[4]) {
  if (0 == strncmp(cover_key, cachekey, sizeof(cover_key) - 1))
    return;

  strncpy(cover_key, cachekey, sizeof(cover_key) - 1);
  cover_key[sizeof(cover_key) - 1] = 0;
  cover_have = false;

  if (gcode_is_alnum(gcode))
    cover_have = load_cover_file(gcode);
}

bool coverart_available(void) {
  return cover_have;
}

uint16_t coverart_width(void) {
  return cover_have ? cover_ww : 0;
}

void coverart_draw(volatile uint8_t *frame) {
  if (!cover_have)
    return;
  // Re-assert our palette every frame: the logo (info tab) shares the
  // MEM_PALETTE[20..235] range and may have overwritten the cube.
  dma_memcpy16(&MEM_PALETTE[CUBE_PAL_BASE], cube_pal, CUBE_NCOLORS);
  unsigned pane_x = COVER_PANE_X + (COVER_MAX_W - cover_ww);   // right-aligned
  for (unsigned r = 0; r < COVER_H; r++)
    dma_memcpy16(&frame[(COVER_PANE_Y + r) * 240 + pane_x],
                 &cover_pix[r * COVER_MAX_W], (cover_ww + 1) / 2);
}
