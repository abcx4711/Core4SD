/*****************************************************************************
 * Core4SD firmware for ATmega328P boards at 16 MHz (Arduino Nano with an
 * SD card reader, e.g. the SPIISD V1)
 *
 * A SmartPort drive emulator for the Apple II with four drives. Each drive
 * holds a disk image from a FAT16/FAT32 SD card, and which image is mounted
 * in which drive is chosen right on the A2 with a little program, the A2let.
 * The A2let lists the SD card's directories through SmartPort STATUS calls
 * and mounts images through CONTROL calls (codes below). It is built into
 * this firmware as a small virtual boot disk, so an Apple with no disk at
 * all can still boot into it.
 *
 *   Drives 1..4  = SmartPort units 1..4 of this device (unit 1 boots).
 *   Boot menu    = drive 1 serves the built-in A2let disk when drive 1 is
 *                  empty, or when the button was pressed (at power-up or
 *                  while the bus is idle) -- until something is mounted in
 *                  drive 1 or the A2let switches the menu off.
 *   Image types  = .PO .HDV (and any file whose size is a multiple of 512),
 *                  .2MG (ProDOS or DOS order), .DSK/.DO (DOS order, 140K).
 *   Persistence  = each drive's image is remembered in EEPROM as an 8.3
 *                  path. A drive that was never set or was ejected stays
 *                  empty.
 *
 * In the code a drive is a "slot" (slots[], NUM_SLOTS); the Apple's own
 * slot, the one the adapter is plugged into, is not used here.
 *
 * Device-specific SmartPort calls (any of our four units answers them):
 *
 *   STATUS $20        INFO, 160 bytes:
 *                       [0] directory depth (0 = root)
 *                       [1] flags: bit0 menu forced, bit1 menu active in
 *                           drive 1, bit7 no SD card
 *                       [2..29] name of the current directory (0-padded)
 *                       [30 + 32*d] drive d: [0] bit0 mounted, bit1 write
 *                           protected; [1..2] blocks; [3] KB of the file
 *                           beyond its blocks (a .2MG header), so the
 *                           drive line shows the file size; [4..31] name
 *   STATUS $40+p      directory page p (p = 0..63), 512 bytes:
 *                       [0] number of entries n (0..15)
 *                       [1] bit0 = another page follows
 *                       [2 + 34*i] entry i: [0] 0 file, 1 directory,
 *                           2 disk image, + $80 read-only file; [1..2]
 *                           FAT directory index;
 *                           [3..5] size in KB; [6..33] name (0-padded)
 *   CONTROL $20 (2)   change directory: index lo, hi of an entry;
 *                     $FFFF = parent, $FFFE = root
 *   CONTROL $21 (3)   mount: drive 0..3, index lo, hi (in the current dir)
 *   CONTROL $22 (1)   eject: drive 0..3
 *   CONTROL $23 (1)   boot menu off (drive 1 boots its image again)
 *   CONTROL $24 (1)   re-initialise the SD card and reload the drives
 *   CONTROL $25 (1)   rotate the drives: 1 -> 2, 2 -> 3, 3 -> 4, 4 -> 1
 *
 * Needs SdFat 2.3 (FAT16/FAT32, long file names).
 *
 * Messages and errors go to the serial port (230400 baud), as much as
 * LOG_LEVEL in config.h allows. Errors of a bus command are printed after
 * its reply has gone out.
 *
 * The bus routines (packet_16mhz.S) and the bus handshake in loop() come
 * from SmartportCFA/SmartportSD by Robert Justice and Andrea Ottaviani.
 *
 * 1.0 September 2026 abcx4711 - Initial Version
 *
 * Authorship commitment (SHA-256):
 * BD0B663E3F5416BBBA76D0E2DDD957D01DD714045B63725CFA1EB30291F0B787
 *****************************************************************************/

/* The firmware version, as in the history above. It goes out in the device
 * information block (STATUS 3), the A2let shows it in its help window
 * ("Core4SD 1.0 on A2ee"), and it is printed on the serial port at start-up. */
#define FW_VERSION_HI 1
#define FW_VERSION_LO 0

#include <Arduino.h>
#include <SdFat.h>
#include <avr/eeprom.h>
#include <avr/pgmspace.h>
#include <string.h>
#include "config.h"
#include "sp_proto.h"
#include "bootimg.h" /* BOOTIMG_BLOCKS, bootimg[] (PROGMEM) */

/* packet_16mhz.S. ReceivePacket: 0 ok, 1 timeout, 2 packet longer than
 * size (read off the bus, not stored); *received (may be NULL) = bytes
 * seen. It does not touch ACK: the handshake is ack_ready()/ack_packet()/
 * ignore_packet() below. SendPacket: 0 ok, 1 timeout (REQ did not rise or
 * fall in time). */
extern "C" unsigned char ReceivePacket(unsigned char *, uint16_t size, uint16_t *received);
extern "C" unsigned char SendPacket(unsigned char *);

#define RX_TIMEOUT 1
#define RX_TOO_LONG 2
#define DATA_RETRIES 25 /* tries for the data packet of WRITE/CONTROL, as SmartportSD */

#define NUM_SLOTS 4
#define PAGE_ENTRIES 15
#define ENTRY_SIZE 34
#define NAME_FIELD 28
#define NAME_TAIL 4 /* a longer name keeps its last 4 characters, e.g. ".2mg" */
#define INFO_SIZE 160
#define PAGE_SIZE 512
#define MAX_DEPTH 8
#define PAGE_CACHE 16

#define SL_MOUNTED 0x01
#define SL_WP 0x02
#define SL_DOS 0x04 /* DOS 3.3 sector order, 280 blocks */

#define EE_MAGIC 0 /* 'S', 'B' */
#define EE_SLOT(s) (16 + (s) * 128)
#define EE_PATH_MAX 127

enum
{
    K_RAW,
    K_2MG,
    K_DOS
};

/* SdFat 2 files cannot be copied, only moved (the source ends up closed). */
#define MOVE(f) static_cast<FatFile &&>(f)

/* The command handlers are kept out of loop(): inlined, all their locals
 * (FatFiles, name buffers) would sit on the stack at the same time, for
 * good. The Nano has about 400 bytes of stack. */
#define NOINLINE __attribute__((noinline))

struct Slot
{
    FatFile f;
    uint16_t blocks;
    uint16_t offset; /* start of the blocks in the file (.2MG) */
    uint8_t flags;
    uint8_t id; /* SmartPort id given by INIT, 0 = none */
};

static uint8_t pb[SP_BUF_SIZE]; /* packet buffer, also scratch */
static SdFat sd;
static Slot slots[NUM_SLOTS];
static uint8_t n_init; /* units that received INIT since reset */
static bool sd_ok;
static bool menu_forced;
static bool no_button; /* CONFIG.TXT: board without a button, A3 never read */

static FatFile cwd;                /* directory the browser is looking at */
static uint16_t dstack[MAX_DEPTH]; /* directory index of each level below root */
static uint8_t depth;
static uint16_t page_start[PAGE_CACHE]; /* entry index where page p starts */
static uint8_t pages_known;

/* ProDOS block (b & 7), half h -> DOS 3.3 sector within the track. */
static const uint8_t DOS_SECT[16] PROGMEM = {
    0x0, 0xE, 0xD, 0xC, 0xB, 0xA, 0x9, 0x8, 0x7, 0x6, 0x5, 0x4, 0x3, 0x2, 0x1, 0xF};

/* ------------------------------------------------------------------------ */
/* Small helpers                                                            */
/* ------------------------------------------------------------------------ */

static bool menu_active(void)
{
    return menu_forced || !(slots[0].flags & SL_MOUNTED);
}

static int8_t slot_of(uint8_t id)
{
    for (uint8_t s = 0; s < NUM_SLOTS; s++)
        if (id && slots[s].id == id)
            return s;
    return -1;
}

/* ------------------------------------------------------------------------ */
/* Serial log                                                               */
/* ------------------------------------------------------------------------ */

/* LOG_LEVEL is set in config.h. The messages are wrapped in if (LOG_...):
 * with the constant false the compiler drops them, strings included. */
#define LOG_ERROR (LOG_LEVEL >= LVL_ERROR)
#define LOG_INFO (LOG_LEVEL >= LVL_INFO)
#define LOG_DEBUG (LOG_LEVEL >= LVL_DEBUG)

#ifdef __AVR__
extern char __heap_start, *__brkval;
#endif

static int free_mem(void)
{
#ifdef __AVR__
    char top;
    return &top - (__brkval ? __brkval : &__heap_start);
#else
    return 0;
#endif
}

/* Stack watermark: stack_paint() fills the free RAM below the stack with
 * STACK_MARK at the start of setup(); stack_reserve() counts the marks the
 * stack has never overwritten since, i.e. the smallest free RAM so far. */
#define STACK_MARK 0xA5

static void stack_paint(void)
{
#ifdef __AVR__
    char top;
    for (char *p = __brkval ? __brkval : &__heap_start; p < &top - 16; p++)
        *p = STACK_MARK;
#endif
}

static int stack_reserve(void)
{
#ifdef __AVR__
    const char *p = __brkval ? __brkval : &__heap_start;
    int n = 0;
    while (*p++ == (char)STACK_MARK)
        n++;
    return n;
#else
    return 0;
#endif
}

/* "<msg>, freemem: n, stack: n", or without "<msg>, " if msg is NULL
 * (stack: the smallest stack reserve so far) */
static void log_free(const __FlashStringHelper *msg)
{
    if (!LOG_INFO)
        return;
    if (msg)
    {
        Serial.print(msg);
        Serial.print(F(", "));
    }
    Serial.print(F("f: "));
    Serial.print(free_mem());
    Serial.print(F(" s: "));
    Serial.println(stack_reserve());
}

static void log_hex(uint8_t v)
{
    Serial.print('$');
    if (v < 0x10)
        Serial.print('0');
    Serial.print(v, HEX);
}

/* The SmartPort error codes of sp_proto.h and their names, both in flash
 * (a switch would become a 94-byte pointer table in RAM). */
static const uint8_t ERR_CODES[] PROGMEM = {
    SP_BADCMD, SP_BUSERR, SP_BADCTL, SP_BADCTLPARM, SP_IOERROR,
    SP_NODRIVE, SP_NOWRITE, SP_BADBLOCK, SP_OFFLINE};
static const char ERR_NAMES[] PROGMEM =
    "BADCMD\0BUSERR\0BADCTL\0BADCTLPARM\0IOERROR\0NODRIVE\0NOWRITE\0BADBLOCK\0OFFLINE";

/* "$2D BADBLOCK"; no name for other values (a command code passed to
 * log_err) */
static void log_errcode(uint8_t err)
{
    const char *name = ERR_NAMES;
    log_hex(err);
    for (uint8_t i = 0; i < sizeof ERR_CODES; i++)
    {
        if (pgm_read_byte(&ERR_CODES[i]) == err)
        {
            Serial.print(' ');
            Serial.print((const __FlashStringHelper *)name);
            return;
        }
        name += strlen_P(name) + 1;
    }
}

/* "E <what> dn bb $err NAME" (s < 0: no drive, blk < 0: no block, err 0:
 * no code); the messages are listed in TECHNICAL.md */
static void log_err(const __FlashStringHelper *what, int8_t s, uint8_t err, int32_t blk = -1)
{
    if (!LOG_ERROR)
        return;
    Serial.print(F("E "));
    Serial.print(what);
    if (s >= 0)
    {
        Serial.print(F(" d"));
        Serial.print(s + 1);
    }
    if (blk >= 0)
    {
        Serial.print(F(" b"));
        Serial.print(blk);
    }
    if (err)
    {
        Serial.print(' ');
        log_errcode(err);
    }
    Serial.println();
}

/* "1 /GAMES/SNAKE2.HDV 65535 P", "3 /GAMES/BLANK.2MG 280 P 64 *": drive and
 * 8.3 path of an image being mounted (log_open), then, once it is mounted,
 * its blocks, D (DOS order) or P (ProDOS order), the offset of block 0 in
 * the file if not 0 (64 for a .2MG), and "*" if it is write protected
 * (log_open_end). Two calls, because the path may sit in pb, which mounting
 * overwrites. */
static void log_open(uint8_t s, const char *path)
{
    if (!LOG_INFO)
        return;
    Serial.print(s + 1);
    Serial.print(' ');
    Serial.print(path);
}

static void log_open_end(uint8_t s)
{
    if (!LOG_INFO)
        return;
    const Slot &sl = slots[s];
    if (sl.flags & SL_MOUNTED)
    {
        Serial.print(' ');
        Serial.print(sl.blocks);
        Serial.print((sl.flags & SL_DOS) ? F(" D") : F(" P"));
        if (sl.offset)
        {
            Serial.print(' ');
            Serial.print(sl.offset);
        }
        if (sl.flags & SL_WP)
            Serial.print(F(" *"));
    }
    Serial.println();
}

static bool button(void)
{
    return digitalRead(EJECT_PIN) == EJECT_ACTIVE;
}

static void put24(uint8_t *p, uint32_t v)
{
    p[0] = v;
    p[1] = v >> 8;
    p[2] = v >> 16;
}

/* ------------------------------------------------------------------------ */
/* Bus                                                                      */
/* ------------------------------------------------------------------------ */

/* The receive handshake. ReceivePacket leaves ACK alone, so a packet is
 * only acknowledged once it is known to be for one of our units; a packet
 * for another device is never acknowledged by us. */
static void ack_ready(void)
{ /* ACK high: ready to receive (level first, then drive it) */
    PORTC |= _BV(5);
    DDRC |= _BV(5);
}

static bool ack_packet(void)
{ /* ACK low: the packet arrived; then the host takes REQ low. Bounded, so
     a host that has gone away cannot hang the Nano. */
    PORTC &= ~_BV(5);
    for (uint32_t i = 0; i < 2000000UL; i++)
        if (!(PIND & 0x04))
            return true;
    return false;
}

static void ignore_packet(void)
{ /* let go of ACK at once (input first, so it is never driven low) */
    DDRC &= ~_BV(5);
    PORTC &= ~_BV(5);
}

static void bus_send(void)
{
    noInterrupts();
    DDRD = 0x40; /* RDDATA as output */
    uint8_t err = SendPacket(pb);
    DDRD = 0x00; /* back to tristate */
    interrupts();
    if (LOG_ERROR && err)
        Serial.println(F("E SEND no REQ"));
}

static void reply(uint8_t id, uint8_t err)
{
    sp_encode(pb, 0, id, SP_TYPE_STATUS, 0x80 | err);
    bus_send();
}

static void reply_data(uint8_t id, uint16_t len, uint8_t type)
{
    sp_encode(pb, len, id, type, 0x80);
    bus_send();
}

/* WRITE and CONTROL: the host sends a data packet right after the command
 * packet. Returns the payload length, -1 on a checksum error or a packet
 * too long for pb, or RX_NONE if no packet came at all -- then it was never
 * handshaken, and the caller must not reply (that would desync the bus). */
#define RX_NONE (-2)
static int bus_receive_data(void)
{
    uint8_t err, tries = 0;
    uint16_t len = 0;
    noInterrupts();
    do
        ack_ready(); /* ready for the data packet */
    while ((err = ReceivePacket(pb, SP_BUF_SIZE, &len)) == RX_TIMEOUT && ++tries < DATA_RETRIES);
    if (err == RX_TIMEOUT)
    {
        ignore_packet();
        interrupts();
        if (LOG_ERROR)
            Serial.println(F("E DATA timeout"));
        return RX_NONE;
    }
    bool acked = ack_packet(); /* it follows our own command: take it */
    interrupts();
    if (!acked)
    {
        if (LOG_ERROR)
            Serial.println(F("E DATA REQ high"));
        return RX_NONE;
    }
    if (err == RX_TOO_LONG)
    {
        if (LOG_ERROR)
        {
            Serial.print(F("E DATA long "));
            Serial.println(len);
        }
        return -1;
    }
    return sp_decode(pb);
}

/* ------------------------------------------------------------------------ */
/* Images                                                                   */
/* ------------------------------------------------------------------------ */

static bool ext_is(const char *sfn, const char *ext)
{
    const char *dot = strrchr(sfn, '.');
    return dot && !strcasecmp(dot + 1, ext); /* SdFat 2 returns lower-case 8.3 names */
}

/* What kind of image `f` is, judged by its 8.3 name and size. Returns true
 * if it looks like a disk image at all (used for the listing). */
static bool image_kind(FatFile &f, uint8_t *kind)
{
    char sfn[13];
    uint32_t size = f.fileSize();
    if (!f.getSFN(sfn, sizeof sfn))
        sfn[0] = 0;
    *kind = K_RAW;
    if (ext_is(sfn, "2MG"))
    {
        *kind = K_2MG;
        return size > 64;
    }
    if (ext_is(sfn, "DSK") || ext_is(sfn, "DO"))
    {
        if (size == 143360UL)
        {
            *kind = K_DOS;
            return true;
        }
        return size && !(size & 511); /* a ProDOS-order .DSK */
    }
    if (ext_is(sfn, "PO") || ext_is(sfn, "HDV"))
        return size && !(size & 511);
    return false;
}

/* Fills in blocks/offset/flags for the freshly opened file of `sl`.
 * Uses pb as scratch. */
static uint8_t analyse(Slot &sl, bool ro)
{
    uint8_t kind;
    uint32_t size = sl.f.fileSize(), blocks;

    if (sl.f.isDir())
        return SP_BADCTLPARM;
    image_kind(sl.f, &kind);
    sl.offset = 0;
    sl.flags = ro ? SL_WP : 0;
    if (kind == K_2MG)
    {
        uint32_t fmt, off, len;
        if (!sl.f.seekSet(0) || sl.f.read(pb, 64) != 64 || memcmp(pb, "2IMG", 4))
            return SP_BADCTLPARM;
        fmt = pb[0x0C] | ((uint32_t)pb[0x0D] << 8);
        off = pb[0x18] | ((uint32_t)pb[0x19] << 8) | ((uint32_t)pb[0x1A] << 16) | ((uint32_t)pb[0x1B] << 24);
        len = pb[0x1C] | ((uint32_t)pb[0x1D] << 8) | ((uint32_t)pb[0x1E] << 16) | ((uint32_t)pb[0x1F] << 24);
        if (pb[0x13] & 0x80)
            sl.flags |= SL_WP; /* locked */
        if (off > 0xFFFF || off > size)
            return SP_BADCTLPARM;
        if (!len || len > size - off)
            len = size - off;
        sl.offset = off;
        if (fmt == 0 && len == 143360UL)
        {
            sl.flags |= SL_DOS;
            blocks = 280;
        }
        else if (fmt == 1)
            blocks = len >> 9;
        else
            return SP_BADCTLPARM; /* nibble image */
    }
    else if (kind == K_DOS)
    {
        sl.flags |= SL_DOS;
        blocks = 280;
    }
    else
    {
        if (size & 511)
            return SP_BADCTLPARM;
        blocks = size >> 9;
    }
    if (!blocks)
        return SP_BADCTLPARM;
    sl.blocks = blocks > 0xFFFF ? 0xFFFF : blocks;
    sl.flags |= SL_MOUNTED;
    return SP_OK;
}

/* Reads or writes block `blk` of slot `sl` from/to pb[0..511]. */
static uint8_t block_io(Slot &sl, uint32_t blk, bool wr)
{
    if (sl.flags & SL_DOS)
    {
        for (uint8_t h = 0; h < 2; h++)
        {
            uint8_t sec = pgm_read_byte(&DOS_SECT[((blk & 7) << 1) + h]);
            uint32_t pos = sl.offset + ((((blk >> 3) << 4) + sec) << 8);
            if (!sl.f.seekSet(pos))
                return SP_IOERROR;
            int n = wr ? (int)sl.f.write(pb + h * 256, 256) : sl.f.read(pb + h * 256, 256);
            if (n != 256)
                return SP_IOERROR;
        }
    }
    else
    {
        if (!sl.f.seekSet(sl.offset + (blk << 9)))
            return SP_IOERROR;
        int n = wr ? (int)sl.f.write(pb, 512) : sl.f.read(pb, 512);
        if (n != 512)
            return SP_IOERROR;
    }
    if (wr && !sl.f.sync())
        return SP_IOERROR;
    return SP_OK;
}

static void unmount(uint8_t s)
{
    slots[s].f.close();
    slots[s].flags = 0;
    slots[s].blocks = 0;
}

/* Opens the 8.3 `path` (from the root) into slot s. Only the directory is
 * opened by its path; the file is looked up in it with openNext(), like the
 * browser lists it. A file SdFat finds by its 8.3 name does not know its
 * long name, one found with openNext() does -- so the drive lines show the
 * long name after a power-up too. `path` is cut at its last '/' for a
 * moment. */
NOINLINE static bool mount_path(uint8_t s, char *path)
{
    Slot &sl = slots[s];
    char *base = strrchr(path, '/');
    char sfn[13];
    FatFile dir;
    uint16_t idx = 0xFFFF; /* not found (yet) */
    bool ro = false, found = false;

    log_open(s, path);
    if (!base)
    {
        log_open_end(s);
        return false;
    }
    *base = 0;
    found = base == path ? dir.openRoot(&sd) : dir.open(&sd, path, O_RDONLY);
    *base++ = '/';
    while (found && sl.f.openNext(&dir, O_RDONLY))
    {
        idx = sl.f.dirIndex();
        bool match = sl.f.getSFN(sfn, sizeof sfn) && !strcasecmp(sfn, base);
        sl.f.close();
        if (match)
            break;
        idx = 0xFFFF;
    }
    if (idx == 0xFFFF || !found)
        found = false;
    else if (!sl.f.open(&dir, idx, O_RDWR))
    {
        ro = true;
        found = sl.f.open(&dir, idx, O_RDONLY);
    }
    dir.close();
    if (!found)
    {
        log_open_end(s);
        if (LOG_ERROR)
            Serial.println(F("E no file"));
        return false;
    }
    uint8_t err = analyse(sl, ro);
    if (err != SP_OK)
    {
        unmount(s);
        log_open_end(s);
        log_err(F("image"), s, err);
        return false;
    }
    log_open_end(s);
    return true;
}

/* The slot's 8.3 path, rebuilt from the directory stack, into EEPROM. */
NOINLINE static void save_path(uint8_t s)
{
    char *p = (char *)pb, sfn[13];
    uint8_t len = 0, k, n;
    FatFile parent, d;
    bool ok = parent.openRoot(&sd);

    for (k = 0; ok && k <= depth; k++)
    {
        if (k < depth)
        {
            ok = d.open(&parent, dstack[k], O_RDONLY) && d.getSFN(sfn, sizeof sfn);
            parent.close();
            parent = MOVE(d); /* d is closed by the move */
        }
        else
        {
            ok = slots[s].f.getSFN(sfn, sizeof sfn);
        }
        if (!ok)
            break;
        n = strlen(sfn);
        if (len + 1 + n > EE_PATH_MAX)
        {
            ok = false;
            break;
        }
        p[len++] = '/';
        memcpy(p + len, sfn, n);
        len += n;
    }
    parent.close();
    if (!ok)
    {
        log_err(F("path long"), s, 0);
        len = 0;
    }
    p[len] = 0;
    for (k = 0; k <= len; k++)
        eeprom_update_byte((uint8_t *)(EE_SLOT(s) + k), p[k]);
}

static void load_slots(void)
{
    if (eeprom_read_byte((uint8_t *)EE_MAGIC) != 'S' || eeprom_read_byte((uint8_t *)EE_MAGIC + 1) != 'B')
    {
        for (uint8_t s = 0; s < NUM_SLOTS; s++)
            eeprom_update_byte((uint8_t *)EE_SLOT(s), 0);
        eeprom_update_byte((uint8_t *)EE_MAGIC, 'S');
        eeprom_update_byte((uint8_t *)EE_MAGIC + 1, 'B');
    }
    for (uint8_t s = 0; s < NUM_SLOTS; s++)
    {
        unmount(s);
        if (!sd_ok)
            continue;
        uint8_t k = 0;
        do
        {
            pb[k] = eeprom_read_byte((uint8_t *)(EE_SLOT(s) + k));
        } while (pb[k] && ++k < EE_PATH_MAX);
        pb[k] = 0;
        if (pb[0] == '/') /* anything else: empty */
            mount_path(s, (char *)pb);
    }
}

/* ------------------------------------------------------------------------ */
/* Directory browsing                                                       */
/* ------------------------------------------------------------------------ */

static void forget_pages(void)
{
    page_start[0] = 0;
    pages_known = 1;
}

/* Reopens cwd from the root along dstack. Stops early (and shortens the
 * stack) if a level has gone. */
static bool reopen_cwd(void)
{
    cwd.close();
    forget_pages();
    if (!sd_ok || !cwd.openRoot(&sd))
    {
        depth = 0;
        return false;
    }
    for (uint8_t k = 0; k < depth; k++)
    {
        FatFile d;
        if (!d.open(&cwd, dstack[k], O_RDONLY) || !d.isDir())
        {
            d.close();
            depth = k;
            return false;
        }
        cwd.close();
        cwd = MOVE(d);
    }
    return true;
}

/* `full` into name[NAME_FIELD + 1]. A name longer than NAME_FIELD keeps its
 * start and its last NAME_TAIL characters, with a '.' for the part left out:
 * "Apple Diskware - ProDOS Assembler Tools (...) [!].2mg" becomes
 * "Apple Diskware - ProDOS..2mg", so the extension stays visible. */
static void fit_name(char *name, const char *full)
{
    size_t n = strlen(full);
    if (n <= NAME_FIELD)
    {
        strcpy(name, full);
        return;
    }
    memcpy(name, full, NAME_FIELD - NAME_TAIL - 1);
    name[NAME_FIELD - NAME_TAIL - 1] = '.';
    memcpy(name + NAME_FIELD - NAME_TAIL, full + n - NAME_TAIL, NAME_TAIL);
    name[NAME_FIELD] = 0;
}

/* The name of f, at most NAME_FIELD characters (see fit_name), into
 * name[NAME_FIELD + 1].
 * SdFat 2 refuses a long name that does not fit the buffer instead of
 * cutting it, so it is read into the spare end of pb (92 bytes, unused
 * while a page or the INFO block is being built) and cut here. A longer
 * name falls back to the 8.3 name. */
#define NAME_SCRATCH_SIZE (SP_BUF_SIZE - SP_MAX_PAYLOAD)
static bool get_name(FatFile &f, char *name)
{
    char *tmp = (char *)pb + SP_MAX_PAYLOAD;
    if (!f.getName(tmp, NAME_SCRATCH_SIZE) && !f.getSFN(tmp, NAME_SCRATCH_SIZE))
        return false;
    fit_name(name, tmp);
    return true;
}

/* Extensions of files the browser does not list (config.h). */
static const char HIDE_EXT[][4] PROGMEM = {HIDE_EXTENSIONS};

/* True if the 8.3 name `sfn` has one of the HIDE_EXTENSIONS. */
static bool hidden_ext(const char *sfn)
{
    const char *dot = strrchr(sfn, '.');
    if (!dot)
        return false;
    for (uint8_t i = 0; i < sizeof HIDE_EXT / sizeof HIDE_EXT[0]; i++)
        if (!strcasecmp_P(dot + 1, HIDE_EXT[i]))
            return true;
    return false;
}

/* The next entry of cwd worth showing, opened into f, its name (at most
 * NAME_FIELD characters) into name[NAME_FIELD + 1] unless name is NULL.
 * Hidden and system entries, names starting with '.' (macOS "._" files) and
 * files with one of the HIDE_EXTENSIONS are skipped. The names are looked at
 * in the scratch end of pb, so callers that only skip entries need no buffer
 * of their own on the stack. */
static bool next_visible(FatFile &f, char *name)
{
    char *tmp = (char *)pb + SP_MAX_PAYLOAD;
    for (;;)
    {
        f.close();
        if (!f.openNext(&cwd, O_RDONLY))
            return false;
        if (f.isHidden() || f.isSystem())
            continue;
        if (!f.isDir() && f.getSFN(tmp, NAME_SCRATCH_SIZE) && hidden_ext(tmp))
            continue;
        if (!f.getName(tmp, NAME_SCRATCH_SIZE) && !f.getSFN(tmp, NAME_SCRATCH_SIZE))
            continue;
        if (tmp[0] == '.' || !tmp[0])
            continue;
        if (name)
            fit_name(name, tmp);
        return true;
    }
}

static void remember_page(uint8_t next)
{
    if (next == pages_known && pages_known < PAGE_CACHE)
    {
        page_start[next] = cwd.curPosition() >> 5;
        pages_known++;
    }
}

NOINLINE static uint8_t build_page(uint8_t p)
{
    FatFile f;
    uint8_t k = pages_known - 1, n = 0, i;

    memset(pb, 0, PAGE_SIZE);
    if (!sd_ok || !cwd.isOpen())
        return SP_NODRIVE;
    if (k > p)
        k = p;
    if (!cwd.seekSet((uint32_t)page_start[k] << 5))
        return SP_IOERROR;
    for (; k < p; k++)
    { /* skip the pages before p */
        for (i = 0; i < PAGE_ENTRIES; i++)
            if (!next_visible(f, NULL))
            {
                f.close();
                return SP_OK;
            } /* empty page */
        remember_page(k + 1);
    }
    while (n < PAGE_ENTRIES)
    {
        uint8_t *e = pb + 2 + n * ENTRY_SIZE;
        /* the name may overrun into the next entry's first byte, which is
         * written later (or stays 0) */
        if (!next_visible(f, (char *)e + 6))
            break;
        uint8_t kind = K_RAW;
        uint32_t size = f.fileSize();
        bool dir = f.isDir();
        bool img = !dir && image_kind(f, &kind);
        e[0] = dir ? 1 : (img ? 2 : 0) | (f.isReadOnly() ? 0x80 : 0);
        e[1] = f.dirIndex();
        e[2] = f.dirIndex() >> 8;
        if (!dir)
            put24(e + 3, (size >> 10) + ((size & 1023) != 0));
        n++;
    }
    if (n == PAGE_ENTRIES)
    {
        remember_page(p + 1);
        pb[1] = next_visible(f, NULL) ? 1 : 0;
    }
    pb[0] = n;
    f.close();
    return SP_OK;
}

NOINLINE static void build_info(void)
{
    memset(pb, 0, INFO_SIZE);
    pb[0] = depth;
    pb[1] = (menu_forced ? 1 : 0) | (menu_active() ? 2 : 0) | (sd_ok ? 0 : 0x80);
    if (depth && cwd.isOpen())
        get_name(cwd, (char *)pb + 2);
    for (uint8_t s = 0; s < NUM_SLOTS; s++)
    {
        uint8_t *e = pb + 30 + s * 32;
        Slot &sl = slots[s];
        e[0] = sl.flags & (SL_MOUNTED | SL_WP);
        e[1] = sl.blocks;
        e[2] = sl.blocks >> 8;
        e[3] = 0;
        if (sl.flags & SL_MOUNTED)
        { /* [3]: the file in KB, as in the list, less the blocks in KB */
            uint32_t size = sl.f.fileSize();
            uint32_t kb = (size >> 10) + ((size & 1023) != 0);
            uint16_t bkb = (sl.blocks >> 1) + (sl.blocks & 1);
            e[3] = kb <= bkb ? 0 : (kb - bkb > 255 ? 255 : kb - bkb);
            get_name(sl.f, (char *)e + 4); /* its 0 lands in the next e[0], set later */
        }
    }
    pb[158] = pb[159] = 0;
}

NOINLINE static uint8_t do_cd(uint16_t idx)
{
    if (!sd_ok)
        return SP_NODRIVE;
    if (idx == 0xFFFF)
    {
        if (depth)
            depth--;
    }
    else if (idx == 0xFFFE)
    {
        depth = 0;
    }
    else
    {
        FatFile d;
        if (depth >= MAX_DEPTH)
            return SP_BADCTLPARM;
        if (!d.open(&cwd, idx, O_RDONLY))
            return SP_BADCTLPARM;
        bool dir = d.isDir();
        d.close();
        if (!dir)
            return SP_BADCTLPARM;
        dstack[depth++] = idx;
    }
    return reopen_cwd() ? SP_OK : SP_IOERROR;
}

/* Entry idx of cwd into slot s. Checked in a scratch slot first: a failed
 * mount keeps the old image. Kept apart from do_mount() so that its scratch
 * slot is off the stack again while save_path() runs. */
NOINLINE static uint8_t mount_index(uint8_t s, uint16_t idx)
{
    Slot tmp;
    bool ro = false;
    if (!tmp.f.open(&cwd, idx, O_RDWR))
    {
        ro = true;
        if (!tmp.f.open(&cwd, idx, O_RDONLY))
            return SP_IOERROR;
    }
    uint8_t err = analyse(tmp, ro);
    if (err)
    {
        tmp.f.close();
        return err;
    }
    slots[s].f.close();
    slots[s].f = MOVE(tmp.f);
    slots[s].blocks = tmp.blocks;
    slots[s].offset = tmp.offset;
    slots[s].flags = tmp.flags;
    return SP_OK;
}

NOINLINE static uint8_t do_mount(uint8_t s, uint16_t idx)
{
    if (s >= NUM_SLOTS)
        return SP_BADCTLPARM;
    if (!sd_ok || !cwd.isOpen())
        return SP_NODRIVE;
    uint8_t err = mount_index(s, idx);
    if (err)
        return err;
    if (s == 0)
        menu_forced = false;
    save_path(s);
    if (pb[0]) /* the path save_path() stored: "2 /GAMES/SNAKE2.DSK 280 D" */
    {
        log_open(s, (const char *)pb);
        log_open_end(s);
    }
    return SP_OK;
}

/* Moves every image one slot on (1 -> 2, 2 -> 3, 3 -> 4, 4 -> 1), with its
 * EEPROM path. The SmartPort ids stay with the slot positions: the Apple
 * sees unit 1 with the image that was in slot 4, and so on. */
NOINLINE static void rotate_slots(void)
{
    Slot last;
    uint8_t k, s;

    last.f = MOVE(slots[NUM_SLOTS - 1].f);
    last.blocks = slots[NUM_SLOTS - 1].blocks;
    last.offset = slots[NUM_SLOTS - 1].offset;
    last.flags = slots[NUM_SLOTS - 1].flags;
    for (s = NUM_SLOTS - 1; s > 0; s--)
    {
        slots[s].f = MOVE(slots[s - 1].f);
        slots[s].blocks = slots[s - 1].blocks;
        slots[s].offset = slots[s - 1].offset;
        slots[s].flags = slots[s - 1].flags;
    }
    slots[0].f = MOVE(last.f);
    slots[0].blocks = last.blocks;
    slots[0].offset = last.offset;
    slots[0].flags = last.flags;

    /* the same with the paths (up to their 0 only: an EEPROM write takes
     * 3.3 ms and the Apple waits for the reply), the last one parked in pb */
    k = 0;
    do
        pb[k] = eeprom_read_byte((uint8_t *)(EE_SLOT(NUM_SLOTS - 1) + k));
    while (pb[k] && ++k < EE_PATH_MAX);
    pb[k] = 0;
    for (s = NUM_SLOTS - 1; s > 0; s--)
    {
        uint8_t c;
        k = 0;
        do
        {
            c = k < EE_PATH_MAX ? eeprom_read_byte((uint8_t *)(EE_SLOT(s - 1) + k)) : 0;
            eeprom_update_byte((uint8_t *)(EE_SLOT(s) + k), c);
        } while (c && ++k <= EE_PATH_MAX);
    }
    for (k = 0; k == 0 || pb[k - 1]; k++)
        eeprom_update_byte((uint8_t *)(EE_SLOT(0) + k), pb[k]);
    if (LOG_INFO)
        Serial.println(F("Slots rotated"));
}

/* Payload length of our control codes (-1: any). */
static int8_t control_len(uint8_t code)
{
    switch (code)
    {
    case 0x20:
        return 2;
    case 0x21:
        return 3;
    case 0x22:
        return 1;
    }
    return -1;
}

NOINLINE static uint8_t do_control(uint8_t code, int len)
{
    /* The data packet carries the control list without its two length
     * bytes. Should a host send them along, they are recognised by the
     * length and dropped. */
    int8_t want = control_len(code);
    if (want > 0 && len == want + 2 && pb[0] == want && pb[1] == 0)
    {
        memmove(pb, pb + 2, want);
        len = want;
    }
    switch (code)
    {
    case 0x00:
    case 0x01:
    case 0x02:
    case 0x03:
    case 0x04:
        return SP_OK; /* standard codes: nothing to do */
    case 0x20:
        if (len != 2)
            return SP_BADCTLPARM;
        return do_cd(pb[0] | (pb[1] << 8));
    case 0x21:
        if (len != 3)
            return SP_BADCTLPARM;
        return do_mount(pb[0], pb[1] | (pb[2] << 8));
    case 0x22:
        if (len != 1 || pb[0] >= NUM_SLOTS)
            return SP_BADCTLPARM;
        unmount(pb[0]);
        eeprom_update_byte((uint8_t *)EE_SLOT(pb[0]), 0);
        return SP_OK;
    case 0x23:
        menu_forced = false;
        return SP_OK;
    case 0x24:
        for (uint8_t s = 0; s < NUM_SLOTS; s++)
            unmount(s);
        cwd.close();
        sd_ok = sd.begin(SD_CONFIG);
        if (LOG_INFO && sd_ok)
            Serial.println(F("SD card re-initialised"));
        if (LOG_ERROR && !sd_ok)
            Serial.println(F("E no SD"));
        depth = 0;
        reopen_cwd();
        load_slots();
        log_free(F("SD read"));
        return sd_ok ? SP_OK : SP_NODRIVE;
    case 0x25:
        rotate_slots();
        return SP_OK;
    }
    return SP_BADCTL;
}

/* ------------------------------------------------------------------------ */
/* Commands                                                                 */
/* ------------------------------------------------------------------------ */

static uint8_t unit_status(uint8_t s, uint16_t *blocks)
{
    if (s == 0 && menu_active())
    {
        *blocks = BOOTIMG_BLOCKS;
        return 0xB4;
    } /* online, read only */
    const Slot &sl = slots[s];
    if (!(sl.flags & SL_MOUNTED))
    {
        *blocks = 0;
        return 0xE8;
    } /* no medium */
    *blocks = sl.blocks;
    return (sl.flags & SL_WP) ? 0xBC : 0xF8;
}

NOINLINE static void cmd_status(uint8_t s, uint8_t id)
{
    uint8_t code = sp_cmd_param(pb, 2);
    uint16_t blocks;
    uint8_t st = unit_status(s, &blocks);

    if (code == 0x00)
    {
        pb[0] = st;
        pb[1] = blocks;
        pb[2] = blocks >> 8;
        pb[3] = 0;
        reply_data(id, 4, SP_TYPE_STATUS);
    }
    else if (code == 0x03)
    { /* device information block */
        pb[0] = st;
        pb[1] = blocks;
        pb[2] = blocks >> 8;
        pb[3] = 0;
        /* length, then 16 characters; split so "\x0E" does not swallow the C.
         * "CORE4SD DRIVE1".."DRIVE4", or "CORE4SD MENU" for the boot menu. */
        memcpy_P(pb + 4, PSTR("\x0E"
                              "CORE4SD DRIVE1  "),
                 17);
        if (s == 0 && menu_active())
            memcpy_P(pb + 13, PSTR("MENU  "), 6);
        else
            pb[18] = '1' + s;
        pb[21] = 0x02; /* hard disk */
        pb[22] = 0x00; /* subtype: no extended calls, removable */
        pb[23] = FW_VERSION_LO;
        pb[24] = FW_VERSION_HI;
        delay(50); /* as SmartportSD does before the DIB */
        reply_data(id, 25, SP_TYPE_STATUS);
    }
    else if (code == 0x20)
    {
        build_info();
        reply_data(id, INFO_SIZE, SP_TYPE_STATUS);
    }
    else if (code >= 0x40 && code < 0x80)
    {
        uint8_t err = build_page(code - 0x40);
        if (err)
            reply(id, err);
        else
            reply_data(id, PAGE_SIZE, SP_TYPE_STATUS);
    }
    else
    {
        reply(id, SP_BADCTL);
    }
}

static uint32_t cmd_block(void)
{
    return sp_cmd_param(pb, 2) | ((uint32_t)sp_cmd_param(pb, 3) << 8) | ((uint32_t)sp_cmd_param(pb, 4) << 16);
}

NOINLINE static void cmd_read(uint8_t s, uint8_t id)
{
    uint32_t blk = cmd_block();
    uint8_t err = SP_OK;
    if (s == 0 && menu_active())
    {
        if (blk >= BOOTIMG_BLOCKS)
            err = SP_BADBLOCK;
        else
            memcpy_P(pb, bootimg + blk * 512, 512);
    }
    else if (!(slots[s].flags & SL_MOUNTED))
    {
        err = SP_OFFLINE;
    }
    else if (blk >= slots[s].blocks)
    {
        err = SP_BADBLOCK;
    }
    else
    {
        /* a read error may be a glitch on the SPI lines: try twice more
           (block_io seeks again each time; writes are not repeated) */
        for (uint8_t t = 0; t < 3; t++)
            if ((err = block_io(slots[s], blk, false)) != SP_IOERROR)
                break;
    }
    if (err)
        reply(id, err);
    else
        reply_data(id, 512, SP_TYPE_DATA);
    if (err)
        log_err(F("READ"), s, err, blk);
}

NOINLINE static void cmd_write(uint8_t s, uint8_t id)
{
    uint32_t blk = cmd_block(); /* before the data packet overwrites it */
    uint8_t err = SP_OK;
    int len = bus_receive_data();
    if (len == RX_NONE)
        return;
    if (len != 512)
        err = SP_BUSERR;
    else if (s == 0 && menu_active())
        err = SP_NOWRITE;
    else if (!(slots[s].flags & SL_MOUNTED))
        err = SP_OFFLINE;
    else if (slots[s].flags & SL_WP)
        err = SP_NOWRITE;
    else if (blk >= slots[s].blocks)
        err = SP_BADBLOCK;
    else
        err = block_io(slots[s], blk, true);
    reply(id, err);
    if (err)
        log_err(F("WRITE"), s, err, blk);
}

NOINLINE static void cmd_control(uint8_t s, uint8_t id)
{
    uint8_t code = sp_cmd_param(pb, 2);
    int len = bus_receive_data();
    if (len == RX_NONE)
        return;
    uint8_t err = len < 0 ? SP_BUSERR : do_control(code, len);
    reply(id, err);
    if (LOG_ERROR && err)
    { /* "E CTL $cc dn $err NAME" */
        Serial.print(F("E CTL "));
        log_hex(code);
        Serial.print(F(" d"));
        Serial.print(s + 1);
        Serial.print(' ');
        log_errcode(err);
        Serial.println();
    }
}

static void cmd_init(uint8_t id)
{
    uint8_t stat = 0x80;
    if (n_init < NUM_SLOTS)
    {
        slots[n_init].id = id;
        n_init++;
        if (n_init == NUM_SLOTS)
            stat = 0xFF; /* last of our units */
    }
    sp_encode(pb, 0, id, SP_TYPE_CMD, stat);
    bus_send();
    if (LOG_DEBUG)
    { /* "I 1 81": drive and the id it got, after the reply */
        Serial.print(F("I "));
        Serial.print(n_init);
        Serial.print(' ');
        Serial.println(id, HEX);
    }
}

/* Waits for ACK (driven by the addressed device) to reach `high`, for about
 * `loops` microseconds. Gives up at once on a bus reset (ph3=0 ph2=1 ph1=0
 * ph0=1): the Apple's ROM resets the bus and sends INIT again after an
 * unacknowledged command, and loop() must not miss that. */
static bool wait_ack(bool high, uint32_t loops)
{
    for (uint32_t i = 0; i < loops; i++)
    {
        if (((PINC & 0x20) != 0) == high)
            return true;
        if (((PIND & 0x3C) >> 2) == 0x05)
            return false;
    }
    return false;
}

/* A packet addressed to another device on the bus: it was not acknowledged
 * (ignore_packet() has already let go of ACK). If that device takes it, stay
 * off ACK until its exchange is over; if nobody takes it within about 2 ms
 * (the Apple's ROM gives up after 0.1 ms and sends it again), go straight
 * back to listening. */
NOINLINE static void not_ours(uint8_t id, uint8_t cmd)
{
    uint8_t edges;
    if (LOG_DEBUG)
    { /* "NO $81 $80": id and command of the packet */
        Serial.print(F("NO "));
        log_hex(id);
        Serial.print(' ');
        log_hex(cmd);
        Serial.println();
    }
    switch (cmd)
    {
    case SP_CMD_STATUS:
    case SP_CMD_FORMAT:
    case SP_CMD_READ:
    case SP_CMD_INIT:
    case 0xC0: /* extended STATUS, READ, FORMAT */
    case 0xC1:
    case 0xC3:
        edges = 3; /* reply packet */
        break;
    case SP_CMD_WRITE:
    case SP_CMD_CONTROL:
    case 0xC2: /* extended WRITE, CONTROL */
    case 0xC4:
        edges = 5; /* data packet, then reply packet */
        break;
    default:
        edges = 0;
        break;
    }
    if (!wait_ack(false, 2000)) /* the other device has the command? */
        return;                  /* nobody: nothing to wait for */
    bool ok = true;
    for (uint8_t i = 0; ok && i < edges; i++)
        ok = wait_ack(!(i & 1), 2000000UL); /* high, low, high, ... (1-2 s each) */
    if (LOG_ERROR && !ok)
    {
        Serial.print(F("E OTHER "));
        log_hex(cmd);
        Serial.println();
    }
}

/* A command packet as ReceivePacket stores it: PBEGIN at [5], DEST (a unit
 * id, never the host) at [6], SRC = host at [7]. */
static bool valid_command(void)
{
    return pb[5] == 0xC3 && pb[6] >= 0x81 && pb[7] == 0x80;
}

/* A command packet has been received into pb, not yet acknowledged, with
 * interrupts off: decide first, then acknowledge it or let it go. */
NOINLINE static void process_packet(void)
{
    if (!valid_command())
    { /* e.g. shifted by a missed sync byte. Not acknowledged: the Apple's
         SmartPort ROM (IIc, IIc Plus, Liron) sends an unacknowledged command
         again, up to about 3000 times, then resets the bus, sends INIT and
         tries once more. An acknowledged one it must get a reply to. */
        ignore_packet();
        interrupts();
        if (LOG_DEBUG)
            Serial.print('?'); /* garbled packets come in bursts while the ROM retries */
        return;
    }
    uint8_t id = pb[6], cmd = pb[14];
    int8_t s = slot_of(id);

    if (cmd == SP_CMD_INIT ? n_init >= NUM_SLOTS : s < 0)
    {
        ignore_packet(); /* never acknowledge another device's packet */
        interrupts();
        not_ours(id, cmd);
        return;
    }
    bool acked = ack_packet(); /* ours: handshake the command packet */
    interrupts();
    if (!acked)
    {
        if (LOG_ERROR)
            Serial.println(F("E CMD REQ high"));
        return;
    }

    switch (cmd)
    {
    case SP_CMD_INIT:
        cmd_init(id);
        break;
    case SP_CMD_STATUS:
        cmd_status(s, id);
        break;
    case SP_CMD_READ:
        cmd_read(s, id);
        break;
    case SP_CMD_WRITE:
        cmd_write(s, id);
        break;
    case SP_CMD_CONTROL:
        cmd_control(s, id);
        break;
    case SP_CMD_FORMAT:
        reply(id, (s == 0 && menu_active()) ? SP_NOWRITE : (slots[s].flags & SL_MOUNTED) ? SP_OK
                                                                                         : SP_OFFLINE);
        break;
    case 0xC2: /* extended WRITE/CONTROL carry data */
    case 0xC4:
        if (bus_receive_data() == RX_NONE)
            break;
        reply(id, SP_BADCMD);
        log_err(F("CMD?"), s, cmd);
        break;
    default:
        reply(id, SP_BADCMD);
        log_err(F("CMD?"), s, cmd);
        break;
    }
}

/* ------------------------------------------------------------------------ */
/* CONFIG.TXT                                                               */
/* ------------------------------------------------------------------------ */

/* Option bits in the first line of /CONFIG.TXT */
#define CFG_ALWAYS_MENU 0x01 /* boot menu at every power-up, as with the button */
#define CFG_NO_BUTTON   0x02 /* no button on the board: A3 floats, never read it */

/* The digit at the start of /CONFIG.TXT ("1", or "3 ; comment"): the option
 * bits CFG_*, 0..9; 0 without the file or without a digit. Read at power-up
 * only. The line goes into the scratch end of pb. */
NOINLINE static uint8_t read_config(void)
{
    FatFile f;
    char *line = (char *)pb + SP_MAX_PAYLOAD;
    int n, i = 0;

    strcpy_P(line, PSTR("/CONFIG.TXT")); /* the name from flash: no RAM for it */
    if (!sd_ok || !f.open(&sd, line, O_RDONLY))
        return 0;
    n = f.read(line, NAME_SCRATCH_SIZE);
    f.close();
    while (i < n && (line[i] == ' ' || line[i] == '\t'))
        i++;
    return (i < n && line[i] >= '0' && line[i] <= '9') ? line[i] - '0' : 0;
}

/* ------------------------------------------------------------------------ */
/* Arduino                                                                  */
/* ------------------------------------------------------------------------ */

void setup(void)
{
    stack_paint(); /* first, for the stack reserve in the log */

    /* Port set-up as in SmartportSD: ACK driven high until the bus loop
     * starts, RDDATA tristate without pull-up, pull-up on WRDATA only. */
    PORTC = 0xFF;
    DDRC = 0xFF;
    PORTD = 0x80;
    DDRD = 0x00;
    ACSR = 0x80;

    if (LOG_ERROR)
        Serial.begin(230400);
    if (LOG_INFO)
    {
        Serial.println();
        Serial.print(F("Core4SD "));
        Serial.print(FW_VERSION_HI);
        Serial.print('.');
        Serial.println(FW_VERSION_LO);
    }

#if PARTITION_LEDS
    pinMode(14, OUTPUT);
    digitalWrite(14, LOW);
    pinMode(15, OUTPUT);
    digitalWrite(15, LOW);
    pinMode(16, OUTPUT);
    digitalWrite(16, LOW);
    pinMode(8, OUTPUT);
    digitalWrite(8, LOW);
#endif
    pinMode(EJECT_PIN, INPUT);

    sd_ok = sd.begin(SD_CONFIG); /* keep going on failure: the menu still boots */
    if (LOG_ERROR && !sd_ok)
        Serial.println(F("E no SD"));
    uint8_t cfg = read_config();
    /* CONFIG.TXT first: on a board without a button A3 floats */
    no_button = cfg & CFG_NO_BUTTON;
    if (LOG_INFO && no_button)
        Serial.println(F("CONFIG.TXT: no button"));
    menu_forced = !no_button && button();
    if (LOG_INFO && menu_forced)
        Serial.println(F("Button pressed: boot menu"));
    if (cfg & CFG_ALWAYS_MENU)
    { /* for boards without a button */
        menu_forced = true;
        if (LOG_INFO)
            Serial.println(F("CONFIG.TXT: boot menu"));
    }
    depth = 0;
    reopen_cwd();
    load_slots();
    log_free(NULL);
}

void loop(void)
{
    PORTD &= ~_BV(6); /* RDDATA low */
    interrupts();
    for (;;)
    {
        DDRC &= ~_BV(5); /* ACK input while the bus is not ours */
        uint8_t phases = (PIND & 0x3C) >> 2;

        if (phases == 0x05)
        { /* bus reset: ph3=0 ph2=1 ph1=0 ph0=1 */
            while (((PIND & 0x3C) >> 2) == 0x05)
                ;
            n_init = 0;
            for (uint8_t s = 0; s < NUM_SLOTS; s++)
                slots[s].id = 0;
            if (LOG_DEBUG)
                Serial.println('R');
            continue;
        }
        if ((phases & 0x0A) != 0x0A)
        { /* bus not enabled: idle */
            if (!menu_forced && !no_button && button())
            {
                delay(30);
                if (button())
                {
                    menu_forced = true; /* next boot comes up in the browser */
                    if (LOG_INFO)
                        Serial.println(F("menu"));
                    while (button())
                        ;
                }
            }
            continue;
        }
        // phase lines for smartport bus enable
        // ph3=1 ph2=x ph1=1 ph0=x
        noInterrupts();
        ack_ready(); /* bus enabled: ready for a command packet */
        uint16_t len = 0;
        uint8_t err = ReceivePacket(pb, SP_BUF_SIZE, &len);
        if (err)
        { /* timeout (also noise), or too long: not acknowledged */
            ignore_packet();
            interrupts();
            if (LOG_ERROR && err == RX_TOO_LONG)
            {
                Serial.print(F("E CMD long "));
                Serial.println(len);
            }
            continue;
        }
        process_packet(); /* acknowledges or ignores it, then interrupts on */
    }
}
