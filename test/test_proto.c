/*
 * Host test for sp_proto.c:
 *  - 512-byte data packets and status/DIB packets must be byte-identical to
 *    the encoders of the proven SmartportSD firmware (copied below);
 *  - every payload length 0..512 must survive encode -> decode.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/sp_proto.h"

static unsigned char packet_buffer[604];

/* ---- legacy encoder from SmartportSD.cpp (encode_data_packet) ---- */
static void legacy_encode_data_packet(unsigned char source)
{
    int grpbyte, grpcount, count;
    unsigned char checksum = 0, grpmsb;
    unsigned char group_buffer[7];
    for (count = 0; count < 512; count++) checksum = checksum ^ packet_buffer[count];
    for (grpcount = 72; grpcount >= 0; grpcount--) {
        memcpy(group_buffer, packet_buffer + 1 + (grpcount * 7), 7);
        grpmsb = 0;
        for (grpbyte = 0; grpbyte < 7; grpbyte++)
            grpmsb = grpmsb | ((group_buffer[grpbyte] >> (grpbyte + 1)) & (0x80 >> (grpbyte + 1)));
        packet_buffer[16 + (grpcount * 8)] = grpmsb | 0x80;
        for (grpbyte = 0; grpbyte < 7; grpbyte++)
            packet_buffer[17 + (grpcount * 8) + grpbyte] = group_buffer[grpbyte] | 0x80;
    }
    packet_buffer[14] = ((packet_buffer[0] >> 1) & 0x40) | 0x80;
    packet_buffer[15] = packet_buffer[0] | 0x80;
    packet_buffer[0] = 0xff; packet_buffer[1] = 0x3f; packet_buffer[2] = 0xcf;
    packet_buffer[3] = 0xf3; packet_buffer[4] = 0xfc; packet_buffer[5] = 0xff;
    packet_buffer[6] = 0xc3; packet_buffer[7] = 0x80; packet_buffer[8] = source;
    packet_buffer[9] = 0x82; packet_buffer[10] = 0x80; packet_buffer[11] = 0x80;
    packet_buffer[12] = 0x81; packet_buffer[13] = 0xC9;
    for (count = 7; count < 14; count++) checksum = checksum ^ packet_buffer[count];
    packet_buffer[600] = checksum | 0xaa;
    packet_buffer[601] = checksum >> 1 | 0xaa;
    packet_buffer[602] = 0xc8;
    packet_buffer[603] = 0x00;
}

/* ---- legacy status reply (4 data bytes) ---- */
static void legacy_status(unsigned char id, const unsigned char *data)
{
    unsigned char checksum = 0; int i, count;
    packet_buffer[0] = 0xff; packet_buffer[1] = 0x3f; packet_buffer[2] = 0xcf;
    packet_buffer[3] = 0xf3; packet_buffer[4] = 0xfc; packet_buffer[5] = 0xff;
    packet_buffer[6] = 0xc3; packet_buffer[7] = 0x80; packet_buffer[8] = id;
    packet_buffer[9] = 0x81; packet_buffer[10] = 0x80; packet_buffer[11] = 0x80;
    packet_buffer[12] = 0x84; packet_buffer[13] = 0x80;
    packet_buffer[14] = 0x80 | ((data[0] >> 1) & 0x40) | ((data[1] >> 2) & 0x20) | ((data[2] >> 3) & 0x10) | ((data[3] >> 4) & 0x08);
    packet_buffer[15] = data[0] | 0x80; packet_buffer[16] = data[1] | 0x80;
    packet_buffer[17] = data[2] | 0x80; packet_buffer[18] = data[3] | 0x80;
    for (i = 0; i < 4; i++) checksum ^= data[i];
    for (count = 7; count < 14; count++) checksum = checksum ^ packet_buffer[count];
    packet_buffer[19] = checksum | 0xaa; packet_buffer[20] = checksum >> 1 | 0xaa;
    packet_buffer[21] = 0xc8; packet_buffer[22] = 0x00;
}

/* ---- legacy write-status / init reply (no data) ---- */
static void legacy_nodata(unsigned char id, unsigned char type, unsigned char stat)
{
    unsigned char checksum = 0; int count;
    packet_buffer[0] = 0xff; packet_buffer[1] = 0x3f; packet_buffer[2] = 0xcf;
    packet_buffer[3] = 0xf3; packet_buffer[4] = 0xfc; packet_buffer[5] = 0xff;
    packet_buffer[6] = 0xc3; packet_buffer[7] = 0x80; packet_buffer[8] = id;
    packet_buffer[9] = type; packet_buffer[10] = 0x80; packet_buffer[11] = stat;
    packet_buffer[12] = 0x80; packet_buffer[13] = 0x80;
    for (count = 7; count < 14; count++) checksum = checksum ^ packet_buffer[count];
    packet_buffer[14] = checksum | 0xaa; packet_buffer[15] = checksum >> 1 | 0xaa;
    packet_buffer[16] = 0xc8; packet_buffer[17] = 0x00;
}

static int fails;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL: " __VA_ARGS__); printf("\n"); fails++; } } while (0)

int main(void)
{
    unsigned char mine[604], data[512];
    int len, t, k;

    srand(1234);
    for (t = 0; t < 200; t++) {
        for (k = 0; k < 512; k++) data[k] = (unsigned char)rand();
        memcpy(packet_buffer, data, 512); legacy_encode_data_packet(0x81 + t % 4);
        memcpy(mine, data, 512); sp_encode(mine, 512, 0x81 + t % 4, SP_TYPE_DATA, 0x80);
        CHECK(!memcmp(mine, packet_buffer, 604), "512 data packet differs (t=%d)", t);

        legacy_status(0x82, data);
        memcpy(mine, data, 4); sp_encode(mine, 4, 0x82, SP_TYPE_STATUS, 0x80);
        CHECK(!memcmp(mine, packet_buffer, 23), "status packet differs (t=%d)", t);
    }
    legacy_nodata(0x83, 0x81, 0x80 | 0x2B);
    sp_encode(mine, 0, 0x83, SP_TYPE_STATUS, 0x80 | 0x2B);
    CHECK(!memcmp(mine, packet_buffer, 18), "write-status packet differs");
    legacy_nodata(0x84, 0x80, 0xFF);
    sp_encode(mine, 0, 0x84, SP_TYPE_CMD, 0xFF);
    CHECK(!memcmp(mine, packet_buffer, 18), "init reply differs");

    /* Round trip: an encoded (tx) packet, seen by the receiver, is the same
     * byte stream shifted by one (C3 at [5] instead of [6]). */
    for (len = 0; len <= 512; len++) {
        unsigned char rx[604 + 8];
        for (k = 0; k < len; k++) data[k] = (unsigned char)rand();
        memcpy(mine, data, len);
        sp_encode(mine, (uint16_t)len, 0x81, SP_TYPE_DATA, 0x80);
        memset(rx, 0xEE, sizeof rx);
        memcpy(rx, mine + 1, 603);
        k = sp_decode(rx);
        CHECK(k == len, "round trip length %d -> %d", len, k);
        CHECK(!memcmp(rx, data, len), "round trip data differs at len %d", len);
        /* one flipped data bit must be caught */
        if (len) {
            memcpy(rx, mine + 1, 603);
            rx[14 + (len % 7 ? 0 : 1)] ^= 0x01;
            CHECK(sp_decode(rx) == -1, "checksum error not detected at len %d", len);
        }
    }
    printf(fails ? "%d FAILURES\n" : "sp_proto: all tests passed\n", fails);
    return fails != 0;
}
