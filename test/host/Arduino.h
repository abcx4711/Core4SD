/* Host mock of the bits of Arduino.h the firmware uses (test only). */
#ifndef MOCK_ARDUINO_H
#define MOCK_ARDUINO_H
#include <stdint.h>
#include <string.h>
#include <stddef.h>

/* PORTC and DDRC count each time ACK (bit 5) starts being driven low:
 * output and 0. The tests check that a packet for another device is never
 * acknowledged. */
extern int mock_ack_lows;
struct AckReg {
    uint8_t v;
    AckReg &operator=(int x);
    AckReg &operator|=(int x) { return *this = v | x; }
    AckReg &operator&=(int x) { return *this = v & x; }
    operator uint8_t() const { return v; }
};
extern AckReg PORTC, DDRC;
extern uint8_t PORTD, DDRD, PIND, PINC, ACSR;
#define _BV(b) (1 << (b))
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
static inline void noInterrupts(void) {}
static inline void interrupts(void) {}
void pinMode(uint8_t, uint8_t);
void digitalWrite(uint8_t, uint8_t);
int digitalRead(uint8_t);
void delay(unsigned long);

/* Serial output is dropped. */
class __FlashStringHelper;
#define F(s) (reinterpret_cast<const __FlashStringHelper *>(s))
#define HEX 16
struct MockSerial {
    void begin(unsigned long) {}
    template <class T> void print(T) {}
    template <class T> void print(T, int) {}
    template <class T> void println(T) {}
    template <class T> void println(T, int) {}
    void println(void) {}
};
static MockSerial Serial;
#endif
