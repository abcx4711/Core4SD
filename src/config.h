/*
 * config.h -- hardware settings for the SPIISD / SmartportSD board.
 *
 * The SmartPort bus lines are fixed by packet_16mhz.S and the port code in
 * main.cpp (same wiring as Robert Justice's SmartportSD):
 *   PD2 = PH0/REQ, PD3 = PH1, PD4 = PH2, PD5 = PH3   (inputs)
 *   PD6 = RDDATA  (output while sending)
 *   PD7 = WRDATA  (input)
 *   PC5 = WRPROT/ACK
 * Only the pins below may be changed.
 */
#ifndef CONFIG_H
#define CONFIG_H

#define SD_CS_PIN         10     /* SD card chip select */
/* SdFat 2.3: dedicated SPI bus, 8 MHz (the fastest the 16 MHz AVR does) */
#define SD_CONFIG         SdSpiConfig(SD_CS_PIN, DEDICATED_SPI, SD_SCK_MHZ(8))
#define EJECT_PIN         17     /* A3: button, see EJECT_ACTIVE */
#define EJECT_ACTIVE      HIGH   /* SmartportSD: button to +5V with a pull-down */

/* Pins the original firmware used for per-partition LEDs. They are driven
 * low at start-up, exactly as before; set to 0 if your board uses them
 * for something else. */
#define PARTITION_LEDS    0

/* Files with these extensions are not listed in the browser (the extension
 * of the 8.3 name, so "Notes for disk 2.txt" counts as TXT too). Up to three
 * characters each, any case; add or remove entries as you like. */
#define HIDE_EXTENSIONS   "TXT", "BAK", "ZIP"

/* Serial log (230400 baud): LVL_NONE nothing, LVL_ERROR errors only,
 * LVL_INFO also start-up and mount messages, LVL_DEBUG also bus details
 * (packets for other devices). */
#define LVL_NONE          0
#define LVL_ERROR         1
#define LVL_INFO          2
#define LVL_DEBUG         3
#ifndef LOG_LEVEL /* also -D LOG_LEVEL=... in build_flags */
#define LOG_LEVEL         LVL_DEBUG
#endif

#endif
