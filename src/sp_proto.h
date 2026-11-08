/*
 * sp_proto.h -- SmartPort packet encoding and decoding (pure C, no Arduino).
 *
 * Buffer layouts, as produced by ReceivePacket/consumed by SendPacket in
 * packet_16mhz.S (Robert Justice's routines):
 *
 *   received (host -> device):
 *     [5]=C3 [6]=DEST [7]=SRC [8]=TYPE [9]=AUX [10]=STAT [11]=ODDCNT
 *     [12]=GRP7CNT [13..] ODDMSB, odd bytes, groups, 2 checksum bytes, C8, 0
 *
 *   to send (device -> host):
 *     [0..5] sync FF 3F CF F3 FC FF, [6]=C3 [7]=DEST [8]=SRC [9]=TYPE
 *     [10]=AUX [11]=STAT [12]=ODDCNT [13]=GRP7CNT [14..] data, checksum, C8, 0
 *
 * A payload of n bytes is sent as n%7 "odd" bytes (preceded by one byte of
 * their MSBs) followed by n/7 groups of seven (each preceded by its MSB byte).
 * The zero byte after C8 tells SendPacket where the packet ends.
 */
#ifndef SP_PROTO_H
#define SP_PROTO_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SP_BUF_SIZE     604     /* 512-byte payload: 14 + 2 + 584 + 2 + 1 + 1 */
#define SP_MAX_PAYLOAD  512

/* Packet types (TYPE byte, as on the wire) */
#define SP_TYPE_CMD     0x80
#define SP_TYPE_STATUS  0x81
#define SP_TYPE_DATA    0x82

/* Commands (byte [14] of a received command packet, as on the wire) */
#define SP_CMD_STATUS   0x80
#define SP_CMD_READ     0x81
#define SP_CMD_WRITE    0x82
#define SP_CMD_FORMAT   0x83
#define SP_CMD_CONTROL  0x84
#define SP_CMD_INIT     0x85

/* SmartPort error codes */
#define SP_OK           0x00
#define SP_BADCMD       0x01
#define SP_BUSERR       0x06
#define SP_BADCTL       0x21
#define SP_BADCTLPARM   0x22
#define SP_IOERROR      0x27
#define SP_NODRIVE      0x28
#define SP_NOWRITE      0x2B
#define SP_BADBLOCK     0x2D
#define SP_OFFLINE      0x2F

/* Encodes the `len` payload bytes at buf[0..len-1] into a complete packet,
 * in place. len <= 512. `stat` is written to the STAT byte unchanged (callers
 * pass 0x80 | error, or the raw INIT reply status). */
void sp_encode(uint8_t *buf, uint16_t len, uint8_t src, uint8_t type, uint8_t stat);

/* Decodes a received packet in place: the payload ends up at buf[0..].
 * Returns the payload length, or -1 if the checksum does not match. */
int sp_decode(uint8_t *buf);

/* Parameter byte n (0..6) of a received command packet, MSB restored.
 * STATUS/CONTROL: n=2 is the status/control code.
 * READ/WRITE:     n=2..4 is the block number (low, middle, high). */
uint8_t sp_cmd_param(const uint8_t *buf, uint8_t n);

#ifdef __cplusplus
}
#endif

#endif
