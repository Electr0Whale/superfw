/*
 * Cover-art / title-screen preview for the ROM browser.  See coverart.h.
 *
 * Reads "/IMGS/{c0}/{c1}/{CODE}.bmp" (up to 136x75, 16bpp X1R5G5B5) directly off
 * the SD card, quantizes it into a per-image adaptive palette (median-cut over
 * a 4:4:4 histogram, refined to exact 5-bit means; MEM_PALETTE[20..235]) and
 * caches the resulting 8bpp image for fast per-frame blits.
 *
 * All SD traffic runs through a per-frame state machine (coverart_pump), so a
 * cover load never blocks the render loop: each step does at most one file
 * operation or a small read chunk, and the menu keeps animating in between.
 */
#include <string.h>
#include <stdbool.h>

#include "gbahw.h"
#include "fatfs/ff.h"
#include "common.h"
#include "nanoprintf.h"
#include "coverart.h"

#define COVER_DIR  "/IMGS"

// Quantized-cover cache: the first load of a cover runs the histogram +
// median-cut + remap and stores the result so later loads only read the
// cached pixels + palette off the SD card.
// Layout (little-endian): magic "CVR1", u16 width, u16 height, u32 src_size,
// u16 src_date, u16 src_time (source BMP stat, for invalidation), then the
// 216-color palette and the 8bpp pixels row by row.
#define COVER_CACHE_DIR   "/.superfw/imgcache"
#define COVER_CACHE_HDRSZ (4 + 2 + 2 + 4 + 2 + 2 + CUBE_NCOLORS * 2)

// Big buffers go in EWRAM (.sbss); the default .bss lives in scarce IWRAM.
#define EWRAM_BSS  __attribute__((section(".sbss")))

static EWRAM_BSS __attribute__((aligned(4))) uint8_t cover_pix[COVER_MAX_W * COVER_H];
static EWRAM_BSS char cover_key[512];     // ROM path the current state belongs to
static bool     cover_have;               // a valid cover is loaded (.bss/IWRAM -> zeroed)
static uint16_t cover_ww;                 // width of the loaded cover
static uint16_t cube_pal[CUBE_NCOLORS];   // the palette, tuned per image (GBA BGR555)

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
    uint16_t oldcount = b->count;
    b->count = box_count(b);
    hi.count = oldcount - b->count;
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

// ---------------------------------------------------------------------------
// Async load state machine. coverart_update()/coverart_update_gcode() only
// record the request; coverart_pump() (called once per rendered frame)
// advances the load one SD operation or a small read chunk at a time, so the
// menu never blocks on the card.

#define CA_ROWS_PER_STEP  8          // rows read/written per pump step
#define CA_RETRY_FRAMES   20         // pause before retrying a failed step

typedef enum {
  CA_IDLE = 0,
  CA_ROM_OPEN,      // preload the ROM header to get the game code
  CA_ROM_READ,
  CA_CACHE_OPEN,    // fast path: the cached quantized cover
  CA_CACHE_HDR,
  CA_CACHE_STAT,
  CA_CACHE_PIX,
  CA_BMP_OPEN,      // slow path: quantize the BMP from scratch
  CA_BMP_HDR,
  CA_BMP_SEEK,
  CA_BMP_PASS1,
  CA_BMP_PASS2,
  CA_WRITE_PIX,     // cache the result (best effort)
  CA_DONE,
  CA_RETRY_WAIT,    // transient SD errors: retry a few times
} e_ca_state;

static e_ca_state ca_state;
static e_ca_state ca_entry;          // where the retry re-enters
static bool  ca_fd_open;
static FIL   ca_fd;
static uint16_t ca_retry_frames;
static uint16_t ca_row;              // the chunk cursor (rows)
static unsigned ca_width, ca_height, ca_rowbytes;
static bool ca_topdown;
static uint32_t ca_dataoff;
static char ca_fpath[512];           // the ROM path to preload
static char ca_bmppath[64];
static char ca_cachepath[64];
static uint8_t ca_gcode[4];
static EWRAM_BSS t_rom_header ca_romh;
static EWRAM_BSS FILINFO ca_fno;
static EWRAM_BSS __attribute__((aligned(4))) uint8_t ca_hdr[COVER_CACHE_HDRSZ];
static EWRAM_BSS uint8_t ca_bmphdr[54];
static EWRAM_BSS uint8_t ca_rowbuf[COVER_MAX_W * 2];

static void ca_make_paths(void) {
  npf_snprintf(ca_bmppath, sizeof(ca_bmppath), "%s/%c/%c/%c%c%c%c.bmp",
               COVER_DIR, ca_gcode[0], ca_gcode[1],
               ca_gcode[0], ca_gcode[1], ca_gcode[2], ca_gcode[3]);
  npf_snprintf(ca_cachepath, sizeof(ca_cachepath), "%s/%c%c%c%c.img",
               COVER_CACHE_DIR, ca_gcode[0], ca_gcode[1], ca_gcode[2], ca_gcode[3]);
}

static void ca_abort(void) {
  if (ca_fd_open) {
    f_close(&ca_fd);
    ca_fd_open = false;
  }
  ca_state = CA_IDLE;
  ca_row = 0;
}

// A step failed: close the file and pause before retrying. The retry keeps
// going indefinitely (real cards hiccup while waking up; the request abort
// cancels it when the selection changes) so the cover eventually shows even
// after a burst of transient SD errors.
static void ca_fail(void) {
  if (ca_fd_open) {
    f_close(&ca_fd);
    ca_fd_open = false;
  }
  ca_retry_frames = CA_RETRY_FRAMES;
  ca_state = CA_RETRY_WAIT;
}

void coverart_pump(void) {
  switch (ca_state) {
  case CA_IDLE:
    break;

  case CA_ROM_OPEN:
    if (FR_OK != f_open(&ca_fd, ca_fpath, FA_READ)) {
      ca_fail();
      break;
    }
    ca_fd_open = true;
    ca_state = CA_ROM_READ;
    break;

  case CA_ROM_READ: {
    UINT rb;
    bool ok = (FR_OK == f_read(&ca_fd, &ca_romh, sizeof(ca_romh), &rb) &&
               rb == sizeof(ca_romh));
    f_close(&ca_fd);
    ca_fd_open = false;
    if (!ok) {
      ca_fail();
      break;
    }
    if (!gcode_is_alnum(ca_romh.gcode)) {
      ca_state = CA_IDLE;
      ca_row = 0;
      break;
    }
    memcpy(ca_gcode, ca_romh.gcode, 4);
    ca_make_paths();
    ca_state = CA_CACHE_OPEN;
    break;
  }

  case CA_CACHE_OPEN:
    if (FR_OK != f_open(&ca_fd, ca_cachepath, FA_READ)) {
      // No cache: quantize the BMP from scratch.
      ca_state = CA_BMP_OPEN;
      break;
    }
    ca_fd_open = true;
    ca_state = CA_CACHE_HDR;
    break;

  case CA_CACHE_HDR: {
    UINT rb;
    bool ok = (FR_OK == f_read(&ca_fd, ca_hdr, sizeof(ca_hdr), &rb) &&
               rb == sizeof(ca_hdr) &&
               ca_hdr[0] == 'C' && ca_hdr[1] == 'V' && ca_hdr[2] == 'R' && ca_hdr[3] == '1');
    if (!ok) {
      f_close(&ca_fd);
      ca_fd_open = false;
      ca_state = CA_BMP_OPEN;
      break;
    }
    ca_width  = ca_hdr[4] | (ca_hdr[5] << 8);
    ca_height = ca_hdr[6] | (ca_hdr[7] << 8);
    ca_state = CA_CACHE_STAT;
    break;
  }

  case CA_CACHE_STAT:
    if (FR_OK == f_stat(ca_bmppath, &ca_fno) &&
        (ca_hdr[8] | ((uint32_t)ca_hdr[9] << 8) | ((uint32_t)ca_hdr[10] << 16) | ((uint32_t)ca_hdr[11] << 24)) == (uint32_t)ca_fno.fsize &&
        (unsigned)(ca_hdr[12] | (ca_hdr[13] << 8)) == ca_fno.fdate &&
        (unsigned)(ca_hdr[14] | (ca_hdr[15] << 8)) == ca_fno.ftime) {
      dma_memcpy16(cube_pal, ca_hdr + 16, CUBE_NCOLORS);
      memset(cover_pix, CUBE_PAL_BASE, sizeof(cover_pix));   // letterbox = black
      ca_row = 0;
      ca_state = CA_CACHE_PIX;
    } else {
      // Stale or corrupt cache: rebuild from the BMP.
      f_close(&ca_fd);
      ca_fd_open = false;
      ca_state = CA_BMP_OPEN;
    }
    break;

  case CA_CACHE_PIX: {
    UINT rb;
    unsigned rows = MIN(CA_ROWS_PER_STEP, ca_height - ca_row);
    unsigned done = 0;
    for (unsigned i = 0; i < rows; i++) {
      if (FR_OK != f_read(&ca_fd, ca_rowbuf, ca_width, &rb) || rb != ca_width)
        break;
      memcpy(&cover_pix[(ca_row + i) * COVER_MAX_W], ca_rowbuf, ca_width);
      done++;
    }
    ca_row += done;
    if (done < rows) {
      ca_fail();
      break;
    }
    // Keep the FatFs object valid while later row chunks are still pending.
    if (ca_row >= ca_height) {
      f_close(&ca_fd);
      ca_fd_open = false;
      ca_state = CA_DONE;
    }
    break;
  }

  case CA_BMP_OPEN:
    if (FR_OK != f_open(&ca_fd, ca_bmppath, FA_READ)) {
      ca_fail();
      break;
    }
    ca_fd_open = true;
    ca_state = CA_BMP_HDR;
    break;

  case CA_BMP_HDR: {
    UINT rb;
    bool ok = (FR_OK == f_read(&ca_fd, ca_bmphdr, sizeof(ca_bmphdr), &rb) &&
               rb == sizeof(ca_bmphdr) &&
               ca_bmphdr[0] == 'B' && ca_bmphdr[1] == 'M');
    if (ok) {
      ca_dataoff = ca_bmphdr[10] | (ca_bmphdr[11] << 8) |
                   (ca_bmphdr[12] << 16) | ((uint32_t)ca_bmphdr[13] << 24);
      int32_t w = ca_bmphdr[18] | (ca_bmphdr[19] << 8) | (ca_bmphdr[20] << 16) | (ca_bmphdr[21] << 24);
      int32_t rh = ca_bmphdr[22] | (ca_bmphdr[23] << 8) | (ca_bmphdr[24] << 16) | (ca_bmphdr[25] << 24);
      unsigned bpp = ca_bmphdr[28] | (ca_bmphdr[29] << 8);
      ca_topdown = rh < 0;
      int32_t h = ca_topdown ? -rh : rh;
      ok = (bpp == 16 && w > 0 && w <= COVER_MAX_W && h > 0 && h <= COVER_H);
      if (ok) {
        ca_width = (unsigned)w;
        ca_height = (unsigned)h;
        ca_rowbytes = ((unsigned)w * 2 + 3) & ~3u;
      }
    }
    f_close(&ca_fd);
    ca_fd_open = false;
    if (!ok) {
      ca_fail();
      break;
    }
    ca_state = CA_BMP_SEEK;
    break;
  }

  case CA_BMP_SEEK:
    if (FR_OK != f_open(&ca_fd, ca_bmppath, FA_READ)) {
      ca_fail();
      break;
    }
    ca_fd_open = true;
    if (FR_OK != f_lseek(&ca_fd, ca_dataoff)) {
      ca_fail();
      break;
    }
    f_stat(ca_bmppath, &ca_fno);   // for the cache header (best effort)
    memset(cover_qbuf, 0, sizeof(cover_qbuf));
    ca_row = 0;
    ca_state = CA_BMP_PASS1;
    break;

  case CA_BMP_PASS1: {
    UINT rb;
    unsigned rows = MIN(CA_ROWS_PER_STEP, ca_height - ca_row);
    unsigned done = 0;
    for (unsigned i = 0; i < rows; i++) {
      if (FR_OK != f_read(&ca_fd, ca_rowbuf, ca_rowbytes, &rb) || rb != ca_rowbytes)
        break;
      for (unsigned x = 0; x < ca_width; x++) {
        unsigned v = ca_rowbuf[x * 2] | (ca_rowbuf[x * 2 + 1] << 8);
        uint8_t *h = qhist((v >> 11) & 0xF, (v >> 6) & 0xF, (v >> 1) & 0xF);
        if (*h != 0xFF)
          (*h)++;
      }
      done++;
    }
    ca_row += done;
    if (done < rows) {
      ca_fail();
      break;
    }
    if (ca_row < ca_height)
      break;
    // Whole image histogrammed: run the cut (a rare, cache-miss-only CPU
    // burst), then rewind for the remap pass.
    cover_cut();
    memset(cover_pix, CUBE_PAL_BASE, sizeof(cover_pix));
    memset(cover_rsum, 0, sizeof(cover_rsum));
    memset(cover_gsum, 0, sizeof(cover_gsum));
    memset(cover_bsum, 0, sizeof(cover_bsum));
    memset(cover_rcnt, 0, sizeof(cover_rcnt));
    if (FR_OK != f_lseek(&ca_fd, ca_dataoff)) {
      ca_fail();
      break;
    }
    ca_row = 0;
    ca_state = CA_BMP_PASS2;
    break;
  }

  case CA_BMP_PASS2: {
    UINT rb;
    unsigned rows = MIN(CA_ROWS_PER_STEP, ca_height - ca_row);
    unsigned done = 0;
    for (unsigned i = 0; i < rows; i++) {
      if (FR_OK != f_read(&ca_fd, ca_rowbuf, ca_rowbytes, &rb) || rb != ca_rowbytes)
        break;
      unsigned dy = ca_topdown ? (ca_row + i) : (ca_height - 1 - (ca_row + i));
      uint8_t *dst = &cover_pix[dy * COVER_MAX_W];
      for (unsigned x = 0; x < ca_width; x++) {
        unsigned v = ca_rowbuf[x * 2] | (ca_rowbuf[x * 2 + 1] << 8);
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
      done++;
    }
    ca_row += done;
    if (done < rows) {
      ca_fail();
      break;
    }
    if (ca_row < ca_height)
      break;
    f_close(&ca_fd);
    ca_fd_open = false;

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

    // Cache the result (best effort; the cover shows regardless).
    f_mkdir(SUPERFW_DIR);
    f_mkdir(COVER_CACHE_DIR);
    f_chmod(COVER_CACHE_DIR, AM_HID, AM_HID);
    ca_hdr[0] = 'C';
    ca_hdr[1] = 'V';
    ca_hdr[2] = 'R';
    ca_hdr[3] = '1';
    ca_hdr[4] = ca_width & 0xFF;
    ca_hdr[5] = (ca_width >> 8) & 0xFF;
    ca_hdr[6] = ca_height & 0xFF;
    ca_hdr[7] = (ca_height >> 8) & 0xFF;
    ca_hdr[8]  = ca_fno.fsize & 0xFF;
    ca_hdr[9]  = (ca_fno.fsize >> 8) & 0xFF;
    ca_hdr[10] = (ca_fno.fsize >> 16) & 0xFF;
    ca_hdr[11] = (ca_fno.fsize >> 24) & 0xFF;
    ca_hdr[12] = ca_fno.fdate & 0xFF;
    ca_hdr[13] = (ca_fno.fdate >> 8) & 0xFF;
    ca_hdr[14] = ca_fno.ftime & 0xFF;
    ca_hdr[15] = (ca_fno.ftime >> 8) & 0xFF;
    dma_memcpy16(ca_hdr + 16, cube_pal, CUBE_NCOLORS);
    ca_row = 0;
    if (FR_OK == f_open(&ca_fd, ca_cachepath, FA_WRITE | FA_CREATE_ALWAYS)) {
      ca_fd_open = true;
      UINT wb;
      f_write(&ca_fd, ca_hdr, sizeof(ca_hdr), &wb);
      ca_state = CA_WRITE_PIX;
    } else {
      ca_state = CA_DONE;
    }
    break;
  }

  case CA_WRITE_PIX: {
    UINT wb;
    unsigned rows = MIN(CA_ROWS_PER_STEP, ca_height - ca_row);
    unsigned done = 0;
    for (unsigned i = 0; i < rows; i++) {
      if (FR_OK != f_write(&ca_fd, &cover_pix[(ca_row + i) * COVER_MAX_W], ca_width, &wb) ||
          wb != ca_width)
        break;
      done++;
    }
    ca_row += done;
    if (done < rows) {
      f_close(&ca_fd);
      ca_fd_open = false;
      f_unlink(ca_cachepath);   // drop the partial file
      ca_state = CA_DONE;
      break;
    }
    // The cache file spans multiple pump calls; close it only after the last row.
    if (ca_row >= ca_height) {
      f_sync(&ca_fd);
      f_close(&ca_fd);
      ca_fd_open = false;
      ca_state = CA_DONE;
    }
    break;
  }

  case CA_DONE:
    cover_ww = (uint16_t)ca_width;
    dma_memcpy16(&MEM_PALETTE[CUBE_PAL_BASE], cube_pal, CUBE_NCOLORS);
    cover_have = true;
    ca_state = CA_IDLE;
    ca_row = 0;
    break;

  case CA_RETRY_WAIT:
    if (--ca_retry_frames == 0)
      ca_state = ca_entry;
    break;
  }
}

void coverart_invalidate(void) {
  cover_key[0] = 0;
  cover_have = false;
  cover_ww = 0;
  ca_abort();
}

void coverart_update(const char *rom_fullpath, uint32_t filesize, bool is_gba) {
  // No-op while the selection hasn't moved (avoids re-reading the SD card).
  if (0 == strncmp(cover_key, rom_fullpath, sizeof(cover_key) - 1))
    return;

  strncpy(cover_key, rom_fullpath, sizeof(cover_key) - 1);
  cover_key[sizeof(cover_key) - 1] = 0;
  cover_have = false;
  cover_ww = 0;

  if (!is_gba) {
    ca_abort();
    return;
  }

  ca_abort();
  ca_entry = CA_ROM_OPEN;
  strncpy(ca_fpath, rom_fullpath, sizeof(ca_fpath) - 1);
  ca_fpath[sizeof(ca_fpath) - 1] = 0;
  ca_state = CA_ROM_OPEN;
}

void coverart_update_gcode(const char *cachekey, const uint8_t gcode[4]) {
  if (0 == strncmp(cover_key, cachekey, sizeof(cover_key) - 1))
    return;

  strncpy(cover_key, cachekey, sizeof(cover_key) - 1);
  cover_key[sizeof(cover_key) - 1] = 0;
  cover_have = false;
  cover_ww = 0;

  ca_abort();
  if (gcode_is_alnum(gcode)) {
    memcpy(ca_gcode, gcode, 4);
    ca_make_paths();
    ca_entry = CA_CACHE_OPEN;
    ca_state = CA_CACHE_OPEN;
  }
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
