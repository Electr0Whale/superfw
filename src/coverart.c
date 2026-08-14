/*
 * Cover-art / title-screen preview for the ROM browser.  See coverart.h.
 *
 * Reads covers exclusively from the packed resource file
 * /.superfw/covers.pak, generated offline by tools/coverart-pack.py (see
 * docs/coverart-pack-acceleration-plan.md for the format). The pack is
 * opened lazily on the first request and kept open; each cover is located
 * with a binary search over the page directory + a cached index page, and
 * its pixels arrive in at most 4KB (8-sector) reads so FatFs takes the
 * multi-sector direct path. A missing, broken or stale pack, a missing
 * game code and malformed entries all fail silently (no cover shown);
 * transient SD errors close and re-open the pack, retrying up to 3 times.
 *
 * All SD traffic runs through a per-frame state machine (coverart_pump), so
 * a cover load never blocks the render loop: each step does at most one file
 * operation or a small read chunk, and the menu keeps animating in between.
 */
#include <string.h>
#include <stdbool.h>

#include "gbahw.h"
#include "fatfs/ff.h"
#include "common.h"
#include "coverart.h"

#define COVER_PACK_PATH   "/.superfw/covers.pak"
#define COVER_PACK_SECTOR 512

// Big buffers go in EWRAM (.sbss); the default .bss lives in scarce IWRAM.
#define EWRAM_BSS  __attribute__((section(".sbss")))

// 512-byte aligned so the pixel batches reach FatFs' multi-sector direct
// read path (disk_read of up to 8 sectors in one go).
static EWRAM_BSS __attribute__((aligned(512))) uint8_t cover_pix[COVER_MAX_W * COVER_H];
static EWRAM_BSS char cover_key[512];     // the request key the state belongs to
static bool     cover_have;               // a valid cover is loaded
static uint16_t cover_ww;                 // width of the loaded cover
static uint16_t cube_pal[CUBE_NCOLORS];   // the cover's palette (GBA BGR555)

// The pack: the global header, the page directory and the file handle stay
// valid for the whole menu session; the index page cache holds the last
// page looked up (binary searches within a page are cheap, reads are not).
#define COVER_PACK_EPP       64          // index entries per page
#define COVER_PACK_MAXPAGES  512         // 512 pages * 64 = 32768 covers
#define COVER_PACK_DIR_BYTES (COVER_PACK_MAXPAGES * 4)   // 2KB directory buffer

typedef struct {
  uint32_t key;
  uint32_t offset;
} t_pack_entry;

static FIL pack_fd;
static bool pack_open, pack_valid, pack_ready, pack_broken;
static uint32_t pack_entries, pack_pages, pack_index_off, pack_data_off, pack_total;
static EWRAM_BSS __attribute__((aligned(4))) uint32_t pack_dir[COVER_PACK_MAXPAGES];
static EWRAM_BSS __attribute__((aligned(4))) t_pack_entry pack_ipage[COVER_PACK_EPP];
static uint32_t pack_ipage_page = ~0u;   // 0xFFFFFFFF: no page cached

static uint32_t ca_key;                  // the requested game code key
static uint32_t ca_page;                 // the index page being looked up
static uint32_t ca_cover_off;            // the found cover block offset
static uint32_t ca_batch;                // the pixels read so far
static uint32_t ca_pixel_total;

static uint32_t rd32(const uint8_t *d) {
  return d[0] | ((uint32_t)d[1] << 8) | ((uint32_t)d[2] << 16) | ((uint32_t)d[3] << 24);
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

#define CA_PIX_PER_STEP  4096      // at most 8 sectors per pump step
#define CA_RETRY_FRAMES  20        // pause before retrying a failed step
#define CA_MAX_RETRIES   3

typedef enum {
  CA_IDLE = 0,
  CA_ROM_OPEN,      // preload the ROM header to get the game code
  CA_ROM_READ,
  CA_PACK_OPEN,     // lazily open + validate the pack, then find the cover
  CA_PACK_HDR,
  CA_PACK_DIR,
  CA_DIR_LOOKUP,    // CPU: binary search the page directory
  CA_INDEX_READ,    // read (and cache) one index page
  CA_INDEX_LOOKUP,  // CPU: binary search the index page
  CA_COVER_HDR,     // read + validate the CVR2 header sector
  CA_COVER_PIX,     // batched compact pixel reads
  CA_EXPAND,        // CPU: compact rows -> COVER_MAX_W stride
  CA_DONE,
  CA_RETRY_WAIT,    // transient SD errors: retry a few times
} e_ca_state;

static e_ca_state ca_state;
static e_ca_state ca_entry;          // where the retry re-enters
static bool  ca_fd_open;             // the transient ROM handle
static FIL   ca_fd;
static uint16_t ca_retry_frames;
static uint16_t ca_retries;
static uint16_t ca_row;              // the expansion cursor (rows)
static unsigned ca_width, ca_height;
static char ca_fpath[512];           // the ROM path to preload
static uint8_t ca_gcode[4];
static EWRAM_BSS t_rom_header ca_romh;
static EWRAM_BSS __attribute__((aligned(4))) uint8_t ca_hdr[COVER_PACK_SECTOR];

static void ca_set_key(void) {
  ca_key = ((uint32_t)ca_gcode[0] << 24) | ((uint32_t)ca_gcode[1] << 16) |
           ((uint32_t)ca_gcode[2] << 8) | ca_gcode[3];
}

static void ca_abort(void) {
  if (ca_fd_open) {
    f_close(&ca_fd);
    ca_fd_open = false;
  }
  ca_state = CA_IDLE;
  ca_row = 0;
}

// A permanent failure: no cover for this item, no retry. The pack handle and
// any already-validated state survive; a new selection starts a fresh lookup.
static void ca_fail_soft(void) {
  ca_state = CA_IDLE;
  ca_row = 0;
}

static void ca_fail_pack(void) {
  pack_broken = true;
  ca_fail_soft();
}

// A transient SD error: close the pack (re-opened on re-entry) and pause a
// few frames; after CA_MAX_RETRIES attempts the item stays blank.
static void ca_fail_disk(e_ca_state entry) {
  if (pack_open) {
    f_close(&pack_fd);
    pack_open = false;
  }
  if (ca_fd_open) {
    f_close(&ca_fd);
    ca_fd_open = false;
  }
  if (++ca_retries > CA_MAX_RETRIES) {
    ca_fail_soft();
    return;
  }
  ca_entry = entry;
  ca_retry_frames = CA_RETRY_FRAMES;
  ca_state = CA_RETRY_WAIT;
}

// Enter the pack lookup from whatever has (or has not) been loaded so far.
static void ca_start_lookup(void) {
  if (pack_broken) {
    ca_fail_soft();
    return;
  }
  if (!pack_open) {
    ca_state = CA_PACK_OPEN;
  } else if (!pack_valid) {
    ca_state = CA_PACK_HDR;
  } else if (!pack_ready) {
    ca_state = CA_PACK_DIR;
  } else {
    ca_state = CA_DIR_LOOKUP;
  }
}

void coverart_pump(void) {
  UINT rb;
  FRESULT res;

  switch (ca_state) {
  case CA_IDLE:
    break;

  case CA_ROM_OPEN:
    res = f_open(&ca_fd, ca_fpath, FA_READ);
    if (res != FR_OK) {
      if (res == FR_NO_FILE || res == FR_NO_PATH || res == FR_INVALID_NAME)
        ca_fail_soft();
      else
        ca_fail_disk(CA_ROM_OPEN);
      break;
    }
    ca_fd_open = true;
    ca_state = CA_ROM_READ;
    break;

  case CA_ROM_READ: {
    bool ok = (FR_OK == f_read(&ca_fd, &ca_romh, sizeof(ca_romh), &rb) &&
               rb == sizeof(ca_romh));
    f_close(&ca_fd);
    ca_fd_open = false;
    if (!ok) {
      ca_fail_disk(CA_ROM_OPEN);
      break;
    }
    if (!gcode_is_alnum(ca_romh.gcode)) {
      ca_fail_soft();
      break;
    }
    memcpy(ca_gcode, ca_romh.gcode, 4);
    ca_set_key();
    ca_start_lookup();
    break;
  }

  case CA_PACK_OPEN:
    if (pack_broken) {
      ca_fail_soft();
      break;
    }
    res = f_open(&pack_fd, COVER_PACK_PATH, FA_READ);
    if (res != FR_OK) {
      if (res == FR_NO_FILE || res == FR_NO_PATH || res == FR_INVALID_NAME) {
        // The pack is absent: every request would fail the same way, so
        // remember it and stop touching the card.
        pack_broken = true;
        ca_fail_soft();
      } else {
        ca_fail_disk(CA_PACK_OPEN);
      }
      break;
    }
    pack_open = true;
    ca_start_lookup();
    break;

  case CA_PACK_HDR: {
    res = f_read(&pack_fd, ca_hdr, COVER_PACK_SECTOR, &rb);
    if (res != FR_OK) {
      ca_fail_disk(CA_PACK_OPEN);
      break;
    }
    bool ok = rb == COVER_PACK_SECTOR &&
              ca_hdr[0] == 'C' && ca_hdr[1] == 'V' &&
              ca_hdr[2] == 'P' && ca_hdr[3] == 'K' &&
              rd32(ca_hdr + 4) == 1 &&
              rd32(ca_hdr + 8) == COVER_PACK_SECTOR &&
              rd32(ca_hdr + 16) == COVER_PACK_EPP &&
              rd32(ca_hdr + 20) <= COVER_PACK_MAXPAGES &&
              rd32(ca_hdr + 12) <= rd32(ca_hdr + 20) * COVER_PACK_EPP &&
              rd32(ca_hdr + 24) == COVER_PACK_SECTOR;
    uint32_t pages = rd32(ca_hdr + 20);
    uint32_t index_off = rd32(ca_hdr + 28);
    uint32_t data_off = rd32(ca_hdr + 32);
    uint32_t total = rd32(ca_hdr + 36);
    if (ok) {
      uint32_t dirbytes = (pages * 4 + 511) & ~511u;
      ok = index_off == COVER_PACK_SECTOR + dirbytes &&
           data_off == index_off + pages * COVER_PACK_SECTOR;
    }
    FSIZE_t fsize = f_size(&pack_fd);
    if (ok && total != (uint32_t)fsize)
      ok = false;
    if (!ok) {
      f_close(&pack_fd);
      pack_open = false;
      pack_broken = true;
      ca_fail_soft();
      break;
    }
    pack_entries = rd32(ca_hdr + 12);
    pack_pages = pages;
    pack_index_off = index_off;
    pack_data_off = data_off;
    pack_total = total;
    pack_valid = true;
    ca_start_lookup();
    break;
  }

  case CA_PACK_DIR: {
    // At most 2KB (512 pages * 4 bytes); the buffer holds it all.
    uint32_t nbytes = pack_pages * 4;
    if (nbytes) {
      res = f_lseek(&pack_fd, COVER_PACK_SECTOR);
      if (res == FR_OK)
        res = f_read(&pack_fd, pack_dir, nbytes, &rb);
      if (res != FR_OK) {
        ca_fail_disk(CA_PACK_OPEN);
        break;
      }
      if (rb != nbytes) {
        ca_fail_pack();
        break;
      }
    }
    pack_ready = true;
    ca_state = CA_DIR_LOOKUP;
    break;
  }

  case CA_DIR_LOOKUP: {
    // The last page whose first key is <= the wanted key.
    uint32_t lo = 0, hi = pack_pages;
    while (lo < hi) {
      uint32_t mid = (lo + hi) >> 1;
      if (pack_dir[mid] <= ca_key)
        lo = mid + 1;
      else
        hi = mid;
    }
    if (lo == 0) {
      ca_fail_soft();               // the key precedes the whole pack
      break;
    }
    ca_page = lo - 1;
    ca_state = CA_INDEX_READ;
    break;
  }

  case CA_INDEX_READ:
    if (pack_ipage_page == ca_page) {
      ca_state = CA_INDEX_LOOKUP;
      break;
    }
    res = f_lseek(&pack_fd, pack_index_off + ca_page * COVER_PACK_SECTOR);
    if (res == FR_OK)
      res = f_read(&pack_fd, pack_ipage, COVER_PACK_SECTOR, &rb);
    if (res != FR_OK) {
      ca_fail_disk(CA_PACK_OPEN);
      break;
    }
    if (rb != COVER_PACK_SECTOR) {
      ca_fail_pack();
      break;
    }
    pack_ipage_page = ca_page;
    ca_state = CA_INDEX_LOOKUP;
    break;

  case CA_INDEX_LOOKUP: {
    uint32_t base = ca_page * COVER_PACK_EPP;
    uint32_t n = pack_entries - base;
    if (n > COVER_PACK_EPP)
      n = COVER_PACK_EPP;
    uint32_t lo = 0, hi = n;
    while (lo < hi) {
      uint32_t mid = (lo + hi) >> 1;
      if (pack_ipage[mid].key < ca_key)
        lo = mid + 1;
      else
        hi = mid;
    }
    if (lo >= n || pack_ipage[lo].key != ca_key) {
      ca_fail_soft();               // no cover for this game code
      break;
    }
    ca_cover_off = pack_ipage[lo].offset;
    ca_state = CA_COVER_HDR;
    break;
  }

  case CA_COVER_HDR: {
    res = f_lseek(&pack_fd, ca_cover_off);
    if (res == FR_OK)
      res = f_read(&pack_fd, ca_hdr, COVER_PACK_SECTOR, &rb);
    if (res != FR_OK) {
      ca_fail_disk(CA_PACK_OPEN);
      break;
    }
    unsigned w = ca_hdr[8] | (ca_hdr[9] << 8);
    unsigned h = ca_hdr[10] | (ca_hdr[11] << 8);
    uint32_t px = rd32(ca_hdr + 12);
    bool ok = rb == COVER_PACK_SECTOR &&
              ca_hdr[0] == 'C' && ca_hdr[1] == 'V' &&
              ca_hdr[2] == 'R' && ca_hdr[3] == '2' &&
              rd32(ca_hdr + 4) == ca_key &&
              w >= 1 && w <= COVER_MAX_W && h >= 1 && h <= COVER_H &&
              px == (uint32_t)w * h &&
              (unsigned)(ca_hdr[16] | (ca_hdr[17] << 8)) == CUBE_NCOLORS &&
              (unsigned)(ca_hdr[18] | (ca_hdr[19] << 8)) == 1 &&
              (ca_cover_off & (COVER_PACK_SECTOR - 1)) == 0 &&
              ca_cover_off >= pack_data_off &&
              ca_cover_off + COVER_PACK_SECTOR + px <= pack_total;
    if (!ok) {
      ca_fail_pack();
      break;
    }
    ca_width = w;
    ca_height = h;
    ca_pixel_total = px;
    ca_batch = 0;
    dma_memcpy16(cube_pal, ca_hdr + 0x14, CUBE_NCOLORS);
    memset(cover_pix, CUBE_PAL_BASE, sizeof(cover_pix));   // letterbox = black
    ca_state = CA_COVER_PIX;
    break;
  }

  case CA_COVER_PIX: {
    uint32_t remaining = ca_pixel_total - ca_batch;
    uint32_t chunk = MIN(remaining, (uint32_t)CA_PIX_PER_STEP);
    res = f_lseek(&pack_fd, ca_cover_off + COVER_PACK_SECTOR + ca_batch);
    if (res == FR_OK)
      res = f_read(&pack_fd, &cover_pix[ca_batch], chunk, &rb);
    if (res != FR_OK) {
      ca_fail_disk(CA_PACK_OPEN);
      break;
    }
    if (rb != chunk) {
      ca_fail_pack();               // the pack is truncated
      break;
    }
    ca_batch += chunk;
    if (ca_batch < ca_pixel_total)
      break;                        // more batches on the following frames
    ca_row = ca_height;
    ca_state = CA_EXPAND;
    break;
  }

  case CA_EXPAND:
    // The pixels were read tightly packed (width bytes per row); spread the
    // rows to the COVER_MAX_W stride from the bottom up, so the destination
    // never overwrites source bytes that still need moving.
    while (ca_row > 0) {
      ca_row--;
      memmove(&cover_pix[ca_row * COVER_MAX_W],
              &cover_pix[ca_row * ca_width], ca_width);
    }
    ca_state = CA_DONE;
    break;

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
  // The pack handle, the validated header and the page directory survive:
  // only the display and the request in flight are dropped.
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
  ca_retries = 0;
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
    ca_set_key();
    ca_retries = 0;
    ca_start_lookup();
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
