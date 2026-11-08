#ifndef MOCK_EEPROM_H
#define MOCK_EEPROM_H
#include <stdint.h>
extern uint8_t mock_eeprom[1024];
extern int mock_eeprom_writes;
static inline uint8_t eeprom_read_byte(const uint8_t *a) { return mock_eeprom[(uintptr_t)a]; }
static inline void eeprom_update_byte(uint8_t *a, uint8_t v)
{
    if (mock_eeprom[(uintptr_t)a] != v) { mock_eeprom[(uintptr_t)a] = v; mock_eeprom_writes++; }
}
#endif
