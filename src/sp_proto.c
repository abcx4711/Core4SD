/*
 * sp_proto.c -- SmartPort packet encoding and decoding. See sp_proto.h.
 */
#include <string.h>
#include "sp_proto.h"

/* MSB byte for up to seven bytes: bit 7 of byte i goes to bit 6-i. */
static uint8_t msb_byte(const uint8_t *b, uint8_t n)
{
    uint8_t m = 0x80, i;
    for (i = 0; i < n; i++)
        m |= (b[i] >> (i + 1)) & (0x80 >> (i + 1));
    return m;
}

void sp_encode(uint8_t *buf, uint16_t len, uint8_t src, uint8_t type, uint8_t stat)
{
    uint8_t odd = (uint8_t)(len % 7);
    uint8_t grps = (uint8_t)(len / 7);
    uint8_t chk = 0, i, oddbuf[6], tmp[7];
    uint16_t k, gstart, end;
    int g;

    for (k = 0; k < len; k++)
        chk ^= buf[k];
    memcpy(oddbuf, buf, odd);

    /* Groups are built from the rear so that no input is overwritten
     * before it is read: group g lands at gstart + 8g, which is always
     * beyond the input of every group below it. */
    gstart = 14 + (odd ? odd + 1 : 0);
    for (g = grps - 1; g >= 0; g--)
    {
        uint8_t *o = buf + gstart + (uint16_t)g * 8;
        memcpy(tmp, buf + odd + (uint16_t)g * 7, 7);
        o[0] = msb_byte(tmp, 7);
        for (i = 0; i < 7; i++)
            o[1 + i] = tmp[i] | 0x80;
    }
    if (odd)
    {
        buf[14] = msb_byte(oddbuf, odd);
        for (i = 0; i < odd; i++)
            buf[15 + i] = oddbuf[i] | 0x80;
    }

    buf[0] = 0xFF;
    buf[1] = 0x3F;
    buf[2] = 0xCF; /* sync */
    buf[3] = 0xF3;
    buf[4] = 0xFC;
    buf[5] = 0xFF;
    buf[6] = 0xC3; /* PBEGIN */
    buf[7] = 0x80; /* DEST: host */
    buf[8] = src;  /* SRC: our unit id */
    buf[9] = type;
    buf[10] = 0x80; /* AUX */
    buf[11] = stat;
    buf[12] = 0x80 | odd;
    buf[13] = 0x80 | grps;
    for (k = 7; k < 14; k++)
        chk ^= buf[k];

    end = gstart + (uint16_t)grps * 8;
    buf[end] = chk | 0xAA;            /* 1 c6 1 c4 1 c2 1 c0 */
    buf[end + 1] = (chk >> 1) | 0xAA; /* 1 c7 1 c5 1 c3 1 c1 */
    buf[end + 2] = 0xC8;              /* PEND */
    buf[end + 3] = 0x00;              /* end of buffer for SendPacket */
}

int sp_decode(uint8_t *buf)
{
    uint8_t odd = buf[11] & 0x7F;
    uint8_t grps = buf[12] & 0x7F;
    uint8_t chk = 0, i, tmp[8], msb, even_bits, odd_bits;
    uint16_t k, gstart, end, len;
    int g;

    if (odd > 6 || grps > 73)
        return -1;
    gstart = 13 + (odd ? odd + 1 : 0);
    end = gstart + (uint16_t)grps * 8;
    if (end + 1 >= SP_BUF_SIZE) /* both checksum bytes must lie inside buf */
        return -1;

    for (k = 6; k < 13; k++) /* DEST .. GRP7CNT */
        chk ^= buf[k];
    even_bits = buf[end] & 0x55;
    odd_bits = (buf[end + 1] & 0x55) << 1;

    if (odd)
    {
        msb = buf[13];
        for (i = 0; i < odd; i++)
            buf[i] = ((msb << (i + 1)) & 0x80) | (buf[14 + i] & 0x7F);
    }
    /* Front to back: the output of group g always lies below its input. */
    for (g = 0; g < grps; g++)
    {
        memcpy(tmp, buf + gstart + (uint16_t)g * 8, 8);
        for (i = 0; i < 7; i++)
            buf[odd + (uint16_t)g * 7 + i] = ((tmp[0] << (i + 1)) & 0x80) | (tmp[1 + i] & 0x7F);
    }

    len = odd + (uint16_t)grps * 7;
    for (k = 0; k < len; k++)
        chk ^= buf[k];
    return chk == (even_bits | odd_bits) ? (int)len : -1;
}

uint8_t sp_cmd_param(const uint8_t *buf, uint8_t n)
{
    return (uint8_t)(((buf[16] << (n + 1)) & 0x80) | (buf[17 + n] & 0x7F));
}
