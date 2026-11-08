#ifndef MOCK_PGMSPACE_H
#define MOCK_PGMSPACE_H
#include <string.h>
#define PROGMEM
#define PSTR(s) (s)
#define pgm_read_byte(p) (*(const uint8_t *)(p))
#define memcpy_P memcpy
#define strcpy_P strcpy
#define strlen_P strlen
#define strcasecmp_P strcasecmp
#endif
