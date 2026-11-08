//*****************************************************************************
//
// Apple //c Smartport Compact Flash adapter
// Written by Robert Justice  email: rjustice(at)internode.on.net
//
// From SmartportCFA/SmartportSD, Copyright (c) 2020-2023 Katherine Stark,
// Robert Justice, Andrea Ottaviani, under the MIT License; Core4SD as a
// whole is under the GNU GPL version 3 or later. See the file LICENSE.
//
// Assembler routines for sending and receiving the smartport packets
// These are timing sensitive and assume a clk frequency of 16Mhz
// 
// Changes abcx4711 August to October / 2026
// - ReceivePacket: timeout while waiting for REQ, buffer
//   size limit (status 2 = too long), received length; does not touch ACK,
//   the caller acknowledges a packet only once its DEST is known
// - SendPacket: timeouts while waiting for REQ high and
//   low (the original waited forever), status 0 = ok, 1 = timeout
// - bit and byte timing unchanged
//*****************************************************************************

// required for register definitions
#include <avr/io.h>        

//*****************************************************************************
// Function: ReceivePacket
// Parameters: packet_buffer pointer (r25:r24), buffer size in bytes, passed by
//             value (r23:r22), pointer to uint16_t receiving the packet length
//             (r21:r20), may be NULL if the caller doesn't need it
// Returns: status (0 = ok, 1 = timeout, 2 = packet was larger than size -
//          truncated, excess bytes were read off the bus but not stored)
// *received: number of bytes that came in on the bus (without the zero
//          terminator we append). For status 2 that is the full length of the
//          oversized packet, for a timeout it is 0.
//
// Description: Same bit timing as the original ReceivePacket, but bounded
// (a timeout instead of waiting forever, never more than size bytes stored)
// and without any access to ACK. It neither raises
// ACK at the start ("ready") nor pulls it low at the end of the packet,
// and does not wait for REQ to go low: the caller drives the handshake -
// ACK high before the call, then either acknowledges the packet (ACK low,
// wait for REQ low) or lets go of ACK, once it knows whether the packet
// is for one of its units. A timeout (status 1) leaves ACK as it was.
//*****************************************************************************
.global ReceivePacket

ReceivePacket:
          mov  XL,r24                 ;mov buffer pointer into X
          mov  XH,r25

          movw r18,r22                ;r19:r18 = size (passed by value in r23:r22, which
                                      ;the code below reuses, so copy it right away)
          clr  r30                    ;Z = running byte counter (bytes seen so far),
          clr  r31                    ;start at 0

                                      ;r21:r20 keep the *received pointer until the end,
                                      ;so the timeout counters use r24/r25 here (free:
                                      ;the buffer pointer is in X already)
          clr  r24                    ;setup timeout counter
          ldi  r25,10                 ;two stage timer, around 1ms

1:        sbic _SFR_IO_ADDR(PIND),2   ;1/2 wait for req line to go high
          rjmp start2                 ;this indicates host is about to send packet
          dec  r24                    ;1
          brne 1b                     ;1/2
          dec  r25
          brne 1b
          rjmp timeout2               ;yes, we have timed out

start2:
          ldi  r22,1                  ; 1  remember tx line status
          clr  r24                    ; Timeout counter low-byte
          ldi  r25,100                ; Timeout counter high-byte (~6ms)

start_wait2:
          sbis _SFR_IO_ADDR(PIND),7   ; Wait for txd line to go low
          rjmp 2f                     ; TXD cleared -> Jump directly to bit sampling!
          dec  r24                    ; Decrement timeout counter
          brne start_wait2
          dec  r25
          brne start_wait2
          rjmp timeout2               ; No start bit within timeout -> Abort instead of hanging!

2:        ldi  r24,10                                        ;1   |delay total of 30 cycles
3:        dec  r24                                                   ;1   | each loop +3 final loop +2
          brne 3b                                                    ;1/2 | 1 + 9x3 + 1x2 = 30

nxtbyte2:                                                            ;    full cycle time for each byte is 32us
          ldi  r25,8                                                 ;1   8bits to read

nxtbit2:  sbic _SFR_IO_ADDR(PIND),7           ;2   ;2    ;1  ;1      ;1/2 now read a bit, cycle time is 4us
          rjmp bitset2                                   ;3  ;3      ;2   bit is set
          rjmp bitclr2                        ;4   ;4                ;2   bit is clr

bitset2:  sbrc r22,0                                     ;4  ;5      ;1/2 test previous bit recv'd
          rjmp carryclr2                                 ;6          ;2   bit set, then we have a zero
          ldi  r22,1                                         ;6      ;1   remember prev tx bit is set
          sec                                                ;7      ;1   else we have a one
          nop                                                ;8      ;1
          nop                                                ;9      ;1
          rjmp loadbit2                                       ;11     ;2

bitclr2:  sbrc r22,0                          ;5   ;6                ;1/2 test previous bit recv'd
          rjmp carryset2                      ;7                     ;2   bit set, then we have a one
          ldi  r22,0                               ;7                ;1   remember prev tx bit is clr
          clc                                      ;8                ;1   else we have a zero
          nop                                      ;9                ;1
          rjmp loadbit2                             ;11               ;2

carryset2: ldi  r22,0                          ;8                     ;1
          sec                                 ;9                     ;1   remember prev tx bit is clr
          rjmp loadbit2                       ;11                    ;2

carryclr2: ldi  r22,1                                    ;7           ;1   remember prev tx bit is set
          clc                                           ;8           ;1
          nop                                           ;9           ;1
          nop                                           ;10          ;1
          nop                                           ;11          ;1

loadbit2: rol  r23                            ;12  ;12  ;12  ;12     ;1   shift bit(carry) into r23
          dec  r25                            ;13  ;13  ;13  ;13     ;1   dec bit counter
          breq havebyte2                      ;14  ;14  ;14  ;14     ;1/2

          ldi  r24,15                                                ;1   |delay total of 45 cycles
3:        dec  r24                                                   ;1   | each loop +3 final loop +2
          brne 3b                                                    ;1/2 | 1 + 14x3 + 1x2 = 45
          nop                                 ;60  ;60  ;60  ;60     ;1
          nop                                 ;61  ;61  ;61  ;61     ;1
          nop                                 ;62  ;62  ;62  ;62     ;1
          rjmp nxtbit2                        ;64  ;64  ;64  ;64     ;2   get next bit. --> 64 cycles = 4us for 16Mhz

havebyte2:
          cp   r30,r18                ;compare bytes-seen-so-far (Z) vs size (r19:r18)
          cpc  r31,r19
          brsh hb2_full               ;buffer already at capacity - don't store, just count it
          st   x+,r23                 ;save byte in buffer
hb2_full:
          adiw r30,1                  ;bytes-seen++ (whether stored or dropped)
          ldi  r25,100                ;timeout counter if we are at the end
          cpi  r22,1                  ;check for status of last bit
          breq wasset2

wasclr2:  sbic _SFR_IO_ADDR(PIND),7   ;now read a bit, wait for transition to 1
          rjmp havesbit2              ;now set, lets get the next byte
          dec  r25
          breq endpkt2                ;we have timed out, must be end of packet
          rjmp wasclr2

wasset2:  sbis _SFR_IO_ADDR(PIND),7   ;now read a bit, wait for transition to 0
          rjmp havesbit2              ;now clr, lets get the next byte
          dec  r25
          breq endpkt2                ;we have timed out, must be end of packet
          rjmp wasset2

havesbit2:                                                           ;    wait for half a bit, 2us (28 cycles total) --> 32 cycles for 16MHz
          ldi  r24,7                                                 ;1   |delay total of 21 cycles
3:        dec  r24                                                   ;1   | each loop +3 final loop +2
          brne 3b                                                    ;1/2 | 1 + 6x3 + 1x2 = 21
          rjmp nxtbyte2                                               ;2   get next byte

endpkt2:  clr  r23
          cp   r30,r18                ;same bounds check for the terminator byte
          cpc  r31,r19
          brsh ep2_full
          st   x+,r23                 ;save zero byte in buffer to mark end
ep2_full:
          adiw r30,1

finish2:  clr  r25
          cp   r18,r30                ;compare size against bytes actually seen
          cpc  r19,r31                ;(Z includes the terminator we appended)
          brlo finish2_overflow       ;size < bytes-seen -> more arrived than fit
          clr  r24                    ;return no error
          rjmp finish2_len
finish2_overflow:
          ldi  r24,2                  ;return status 2: packet didn't fit in size
finish2_len:
          sbiw r30,1                  ;don't count the terminator we appended
          mov  r0,r20                 ;*received == NULL? then nothing to report
          or   r0,r21
          breq finish2_ret
          movw r26,r20                ;X = received (buffer pointer isn't needed anymore)
          st   x+,r30                 ;*received = number of bytes read from the bus
          st   x,r31
finish2_ret:
          ret

timeout2:
          mov  r0,r20                 ;*received == NULL?
          or   r0,r21
          breq timeout2_ret
          movw r26,r20                ;a timeout can only happen before the first byte,
          st   x+,r30                 ;so Z is still 0: report 0 bytes
          st   x,r31
timeout2_ret:
          ldi  r24,1                  ;setup return value, 1=error
          clr  r25
          ret

//*****************************************************************************
// Function: SendPacket
// Parameters: packet_buffer pointer
// Returns: status (0 = ok, 1 = timeout: REQ never went high before the packet was
//          sent, or REQ never went low again after it was sent)
//
// Description: Handles the ACK and REQ lines and sends the packet in
// packet_buffer (up to its zero byte). Same bit timing as the original
// SendPacket. It waits up to about 200ms for REQ to go high before sending
// (the IIgs can take well over 1ms; the original waited forever), and the
// final wait for REQ to go low is really bounded (about
// 16ms): there the counter was reloaded on every pass, so it never reached 0
// and the loop waited forever if the Apple didn't lower REQ - with interrupts
// disabled that froze the Nano completely. On a timeout ACK is already low
// again when we return.
//*****************************************************************************
.global SendPacket

SendPacket:
          mov  XL,r24                 ;mov buffer pointer into X
          mov  XH,r25

          sbi  _SFR_IO_ADDR(PORTC),5  ;set ACK high to signal we are ready to send

          ldi  r22,255                ;setup timeout counter, three stages, around 200ms:
2:        clr  r21                    ;the IIgs SmartPort firmware can take well over
          ldi  r20,10                 ;1ms to raise REQ (the IIc ROM does it at once)
1:        sbic _SFR_IO_ADDR(PIND),2   ;wait for req line to go high
          rjmp contin_s2              ;this indicates host is ready to receive packet
          dec  r21
          brne 1b
          dec  r20
          brne 1b
          dec  r22
          brne 2b
          cbi  _SFR_IO_ADDR(PORTC),5  ;timed out - drop ACK back low instead of leaving
          rjmp error_s2               ;it stuck high, then give up instead of hanging forever

contin_s2:
nxtsbyte_s2:
          ld   r23,x+                 ;2   get first byte from buffer
          cpi  r23,0                  ;1   zero marks end of data
          breq endspkt_s2             ;1/2

          ldi  r25,8                  ;1   8bits to read
nxtsbit_s2:
          sbrs r23,7                  ;1/2 send bit 7 first
          rjmp sbitclr_s2             ;2   bit is clear
          sbi  _SFR_IO_ADDR(PORTD),6  ;2   set bit for 1us (14 cycles)-->16 cycles for 16Mhz(2 more)

          ldi  r24,4                  ;1   |delay total of 12 cycles
3:        dec  r24                    ;1   | each loop +3 final loop +2
          brne 3b                     ;1/2 | 1 + 3x3 + 1x2 = 11

          nop                         ;1
          nop                         ;1

          cbi  _SFR_IO_ADDR(PORTD),6  ;2   clr bit for 3us (42 cycles)-->48 cycles for 16Mhz(6 more)

          dec  r25                    ;1   dec bit counter
          breq nxtsbyt1_s2            ;1/2
          rol  r23                    ;1

          ldi  r24,13                 ;1   |delay total of 39 cycles
3:        dec  r24                    ;1   |  each loop +3 final loop +2
          brne 3b                     ;1/2 |  1 + 12x3 + 1x2 = 39

          rjmp nxtsbit_s2             ;2

nxtsbyt1_s2:                          ;    delay to makeup 3us (42 cycles total)-->48 cycles for 16Mhz(6 more)
          ldi  r24,11                 ;1   |delay total of 33 cycles
3:        dec  r24                    ;1   | each loop +3 final loop +2
          brne 3b                     ;1/2 | 1 + 10x3 + 1x2 = 33

          nop                         ;1

          rjmp nxtsbyte_s2            ;2

; bit is clr, we need to check if its the last one, otherwise delay for 4us before next bit
sbitclr_s2:
          dec  r25                    ;1
          breq nxtsbycl_s2            ;1/2 end of byte, delay then get nxt
          rol  r23                    ;1
                                      ;    delay to makeup 4us (56 cycles total)-->64 cycles for 16Mhz(8 more)
          ldi  r24,18                 ;1   |delay total of 54 cycles
3:        dec  r24                    ;1   | each loop +3 final loop +2
          brne 3b                     ;1/2 | 1 + 17x3 + 1x2 = 54

          nop                         ;1
          nop                         ;1
          rjmp nxtsbit_s2             ;2

nxtsbycl_s2:                          ;    delay to makeup 4us (56 cycles total)-->64 cycles for 16Mhz(8 more)
          ldi  r24,16                 ;1   |delay total of 48 cycles
3:        dec  r24                    ;1   | each loop +3 final loop +2
          brne 3b                     ;1/2 | 1 + 15x3 + 1x2 = 48

          nop                         ;1
          nop                         ;1
          nop                         ;1

          rjmp nxtsbyte_s2            ;2

endspkt_s2:
          cbi  _SFR_IO_ADDR(PORTC),5  ;set ACK(BSY) low to signal we have sent the pkt

          clr  r21                    ;wait for REQ to go low again, two-stage timer:
          ldi  r20,200                ;200 * 256 passes * 5 cycles = about 16ms
1:        sbis _SFR_IO_ADDR(PIND),2   ;wait for REQ line to go low
          rjmp finishs_s2             ;this indicates host has acknowledged ACK
          dec  r21
          brne 1b
          dec  r20
          brne 1b
          rjmp error_s2               ;timeout, REQ never went low

finishs_s2:
          clr  r25
          clr  r24                    ;return no error
          ret

error_s2: clr  r25
          ldi  r24,1                  ;return error (timeout)
          ret


        

