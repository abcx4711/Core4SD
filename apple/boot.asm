; ---------------------------------------------------------------------------
; boot.asm -- boot block for the Core4SD SD-Browser (64tass)
;
;   64tass -C --nostart -D NBLK=5 -o boot.bin boot.asm
;
; The Apple's boot code loads block 0 to $0800 and jumps to $0801 with
; X = slot * 16, plus $80 when it booted drive 2 of the slot (the IIc Plus
; boots its external SmartPort chain as slot 5, drive 2: X = $D0, its
; internal 3.5" drive being drive 1). This block reads the blocks listed in
; `blktab` one after the other to $2000 and starts the browser at $2003.
;
; Two kinds of boot device:
;   - SmartPort / ProDOS block device ($CnFF <> 0): the whole block is
;     loaded; blocks are read through the slot's ProDOS block driver
;     ($Cn00 + $CnFF).
;   - Disk II ($CnFF = 0, e.g. CORE4SD.PO in a 5.25" drive of an emulator):
;     the ROM loads only physical sector 0 (the first half of this block).
;     Like ProDOS, the first half has the ROM fetch physical sector 2 (the
;     second half) to $0900, then every half block is read through the
;     ROM's sector reader at $Cn5C, which returns through $0801 each time;
;     `stage` tells the entries apart. The head is moved by `seek`.
;
; Two users:
;   - the firmware's built-in menu disk: the browser sits in blocks 1..NBLK,
;     which is what the table holds as assembled;
;   - CORE4SD.PO (tools/mkpo.py): a normal ProDOS volume; the tool writes the
;     data blocks of CORE4SD.SYSTEM into `cnt`/`blktab`, so the image both
;     boots straight into the browser and holds CORE4SD.SYSTEM for ProDOS.
; ---------------------------------------------------------------------------

        .cpu "6502"

        .weak
NBLK    = 5
        .endweak

MAXBLK  = 32                    ; table size: up to 16 KB of program

WAIT    = $FCA8                 ; monitor delay, A = time
PH_OFF  = $C080                 ; + slot*16 + phase*2: stepper phase off
PH_ON   = $C081                 ;                                    on
MOTOROFF = $C088                ; + slot*16

; zero page used by the Disk II boot ROM's sector reader
BUF     = $26                   ; destination (lo must be 0)
SLOT16  = $2B                   ; slot * 16
SECTOR  = $3D                   ; physical sector wanted
TRACK   = $41                   ; track wanted

        * = $0800

        .byte $01               ; Disk II ROM: sectors to load
        ldy stage
        beq first
        jmp d2_next             ; back from the ROM's sector reader
first   stx unit            ; with the drive bit: the unit for the driver
        txa
        and #$70                ; the slot alone: $D0 must give $C5, not $CD
        lsr a
        lsr a
        lsr a
        lsr a
        ora #$C0
        sta rdff+2
        sta drv+1
        sta romhi
rdff    lda $C0FF               ; patched to $CnFF: driver entry offset
        bne blockdev
        inc stage               ; Disk II: fetch the second half first
        lda #0
        sta track
        lda #2
        ldy #>$0900
        ; fall through

; Reads physical sector A of `track` to page Y through the Disk II ROM,
; which then jumps to $0801 again. The head must be on `track`.
d2_read sta SECTOR
        clc
        adc #1                  ; the ROM stops once SECTOR reaches ($0800)
        sta $0800
        sty BUF+1
        lda #0
        sta BUF
        lda track
        sta TRACK
        ldx unit
        lda romhi
        pha
        lda #$5B                ; RTS goes to $Cn5C
        pha
        rts

; --- SmartPort / ProDOS block device ---------------------------------------

blockdev
        sta drv
        lda #$20
        sta dst
        lda #0
        sta idx
loop    lda #1                  ; READ
        sta $42
        lda unit
        sta $43
        lda #0
        sta $44
        lda dst
        sta $45
        ldy idx
        lda blktab,y
        sta $46
        lda blktab+1,y
        sta $47
        jsr call
        bcs fail
        inc dst
        inc dst
        inc idx
        inc idx
        dec cnt
        bne loop
        jmp $2003

call    jmp (drv)

fail    ldx #0
-       lda msg,x
        beq hang
        sta $0400,x
        inx
        bne -
hang    jmp hang

        .enc "apple"            ; high bit set, for the text screen
        .cdef " ~", $A0
msg     .null "CORE4SD: BOOT ERROR"
        .enc "none"

drv     .word 0
unit    .byte 0
romhi   .byte 0
stage   .byte 0                 ; Disk II: 0 first entry, 1 second half loading, 2 program
track   .byte 0
dst     .byte 0
idx     .byte 0

        .cerror * > $0900, "the first half of the boot block is full"

; --- Disk II, second half ----------------------------------------------------

d2_next lda stage
        cmp #2
        beq d2_adv
        inc stage               ; block 0 is complete: load the program
        lda #$20
        sta dst
        lda #0
        sta idx
        sta half
        sta curht
        beq d2_blk

d2_adv  inc dst                 ; next half block
        lda half
        eor #1
        sta half
        bne d2_blk
        inc idx
        inc idx
        dec cnt
        bne d2_blk
        ldx unit
        lda MOTOROFF,x
        jmp $2003

d2_blk  ldy idx                 ; track = block / 8
        lda blktab+1,y
        sta tmp
        lda blktab,y
        pha
        lsr tmp
        ror a
        lsr tmp
        ror a
        lsr tmp
        ror a
        sta track
        jsr seek
        pla                     ; physical sector of this half
        and #7
        asl a
        ora half
        tax
        lda phys,x
        ldy dst
        jmp d2_read

; Moves the head from half track `curht` to `track`.
seek    lda track
        asl a
        sta tmp
_step   lda curht
        cmp tmp
        beq _done
        bcc _in
        sbc #1                  ; carry is set
        bcs _move
_in     adc #1                  ; carry is clear
_move   pha
        jsr ph_on
        lda #$40
        jsr WAIT
        lda curht
        jsr ph_off
        pla
        sta curht
        jmp _step
_done   lda #$40
        jsr WAIT
        lda curht
        ; fall through: last phase off

ph_off  and #3
        asl a
        ora unit
        tax
        lda PH_OFF,x
        rts

ph_on   and #3
        asl a
        ora unit
        tax
        lda PH_ON,x
        rts

; ProDOS sector (block & 7) * 2 + half -> physical sector
phys    .byte 0, 2, 4, 6, 8, 10, 12, 14, 1, 3, 5, 7, 9, 11, 13, 15

half    .byte 0
curht   .byte 0
tmp     .byte 0

        .cerror * > $09BF, "the boot block is full"
        .fill $09BF - *, 0      ; fixed place for tools/mkpo.py
cnt     .byte NBLK
blktab  .word range(1, NBLK + 1)
        .fill $0A00 - *, 0
