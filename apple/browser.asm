; ---------------------------------------------------------------------------
; browser.asm -- Core4SD SD-Browser for the Apple II (64tass, plain 6502)
;
;   64tass --nostart -o CORE4SD.SYSTEM browser.asm
;
; Lists the directories of the SD card in the SPIISD adapter and mounts disk
; images into its four slots, through the SmartPort STATUS/CONTROL calls of
; the Core4SD firmware (see src/main.cpp).
;
; One binary, two ways in:
;   $2000  ProDOS system program (CORE4SD.SYSTEM, type SYS, loaded at $2000)
;   $2003  from the boot block of the firmware's built-in menu disk
;
; Keys: up/down select, left/ESC parent directory, right/RETURN open a
; directory (or ask for a slot on a file), 1-4 mount into slot 1-4,
; E eject, R rotate the slots (1->2, 2->3, 3->4, 4->1), S re-read the SD
; card, B boot, Q quit, ? or H help. Q is only offered when the browser was
; started from a running ProDOS (booted from the menu it could only reboot,
; like B), and not after R: ProDOS would come back to a different image in
; its drive.
; ---------------------------------------------------------------------------

        .cpu "6502"

KBD      = $C000
KBDSTRB  = $C010
CLR80COL = $C000        ; write: 80STORE off
CLR80VID = $C00C        ; write: 40 columns
CLRALTCH = $C00E        ; write: primary character set
SETALTCH = $C00F        ; write: alternate set (inverse lower case)
TXTSET   = $C051
TXTPAGE1 = $C054
MLI      = $BF00

PAGEBUF  = $1000        ; directory page, 512 bytes
INFOBUF  = $1200        ; INFO, 160 bytes
DIBBUF   = $1300        ; unit count / device information block
SAVEBUF  = $1400        ; the screen under the help window, HELPW * HELPH

HELPW    = 34           ; help window: size and position (centred)
HELPH    = 20
HELPX    = (40 - HELPW) / 2
HELPY    = 2            ; rows 2..21: over the list, status, "Drives", drives 1-3

ptr      = $06          ; entry pointer (also used for the boot jump)
ptr2     = $08          ; string pointer
scrp     = $EB          ; current screen line

NENT     = 15           ; entries per page
ESIZE    = 34           ; bytes per entry
LISTROW  = 2            ; first list row (row 0 title, row 1 path)
STATROW  = 17           ; page and messages, row 18 "Drives ----"
SLOTROW  = 19           ; the four drive lines, row 23 the key line

ST_INFO  = $20          ; firmware status/control codes
ST_PAGE  = $40
CT_CD    = $20
CT_MOUNT = $21
CT_EJECT = $22
CT_MENU  = $23
CT_RESCAN = $24
CT_ROTATE = $25

; Prints the zero-terminated string at `addr`.
print   .macro addr
        lda #<\addr
        sta ptr2
        lda #>\addr
        sta ptr2+1
        jsr puts
        .endm

; Jumps to `target` if A = `code`, whatever the distance.
key     .macro code, target
        cmp #\code
        bne +
        jmp \target
+
        .endm

; CONTROL call with the control list at `list`.
control .macro list, code
        lda #<\list
        sta sp_buf
        lda #>\list
        sta sp_buf+1
        lda #\code
        jsr sp_control
        .endm

        * = $2000
        jmp start_prodos        ; $2000
        jmp start_boot          ; $2003

start_boot:
        lda #1
        bne common
start_prodos:
        lda #0
common: sta booted
        ldx #1                  ; Q only to go back to a running ProDOS
        cmp #0
        bne +
        lda MLI
        cmp #$4C
        bne +
        dex
+       stx noquit
        ldx #0                  ; monitor ROM id: $06 = IIe, IIc, IIgs; else
        lda $FBB3               ; II or II+, whose character ROM has no
        cmp #$06                ; lower case: putc writes upper case instead
        beq +
        ldx #$80
+       stx upcase
        jsr detect_model
        cld
        sta CLR80COL
        sta CLR80VID
        sta SETALTCH
        bit TXTSET
        bit TXTPAGE1
        lda #0
        sta inv
        jsr cls
        ldx #0
        ldy #0
        jsr gotoxy
        #print t_search
        jsr find_dev
        bcc found
        ldx #0
        ldy #2
        jsr gotoxy
        #print t_nodev
        ldx #0
        ldy #4
        jsr gotoxy
        #print t_anykey
        jsr getkey
        jmp quit

found:  lda #0
        sta page
        sta sel
reload_all:
        jsr load_info
        jsr load_page
        jsr draw_all
main:   jsr getkey
        cmp #$E0                ; lower case -> upper case
        bcc +
        and #$DF
+       cmp #$8B                ; up/down with Open-Apple held (IIe, IIc,
        beq +                   ; IIgs): a whole page. Shift cannot be told
        cmp #$8A                ; apart, it gives the same key codes.
        bne _keys
+       bit upcase              ; II/II+: no Open-Apple key to read
        bmi _keys
        bit $C061               ; Open-Apple pressed?
        bpl _keys
        cmp #$8B
        bne +
        jmp k_pgup
+       jmp k_pgdn
_keys:  #key $8B, k_up
        #key $8A, k_down
        #key $AC, k_pgup         ; , or < : previous page (all models)
        #key $BC, k_pgup
        #key $AE, k_pgdn         ; . or > : next page
        #key $BE, k_pgdn
        #key $88, k_left
        #key $9B, k_left
        #key $95, k_open
        #key $8D, k_open
        #key $C5, k_eject        ; E
        #key $C2, k_boot         ; B
        #key $D1, k_quit         ; Q
        #key $D2, k_rotate       ; R
        #key $D3, k_rescan       ; S
        #key $89, k_nextdev      ; Tab
        #key $BF, k_help         ; ?
        #key $C8, k_help         ; H
        cmp #$B1                ; 1..4
        bcc to_main
        cmp #$B5
        bcs to_main
        sec
        sbc #$B1
        tax
        jmp mount_sel
to_main:
        jmp main                ; for branches that cannot reach main

; --- navigation --------------------------------------------------------------

k_up:   lda sel
        beq _prev
        dec sel
        jmp redraw_list
_prev:  lda page
        beq to_main
        dec page
        jsr load_page
        ldx nent
        beq +
        dex
+       stx sel
        jmp redraw_list

k_down: ldx sel
        inx
        cpx nent
        bcs _next
        stx sel
        jmp redraw_list
_next:  lda more
        beq to_main
        lda page
        cmp #63
        bcs to_main
        inc page
        jsr load_page
        lda #0
        sta sel
        jmp redraw_list

; A page back, the selection on its first entry (on page 1: the first entry).
k_pgup: lda page
        beq _top
        dec page
        jsr load_page
_top:   lda #0
        sta sel
        jmp redraw_list

; A page on, the selection on its first entry (on the last page: the last).
k_pgdn: lda more
        beq _last
        lda page
        cmp #63
        bcs _last
        inc page
        jsr load_page
        lda #0
        sta sel
        jmp redraw_list
_last:  ldx nent
        beq +
        dex
+       stx sel
        jmp redraw_list

redraw_list:
        jsr draw_list
        jsr draw_status
        jmp main

k_left: lda #$FF
        sta ctl_cd_idx
        sta ctl_cd_idx+1
        jmp do_cd

k_open: lda nent
        beq main2
        jsr set_eptr
        ldy #0
        lda (ptr),y
        and #$7F                ; bit7: read-only
        cmp #1                  ; directory?
        bne _file
        ldy #1
        lda (ptr),y
        sta ctl_cd_idx
        iny
        lda (ptr),y
        sta ctl_cd_idx+1
        jmp do_cd
_file:  jsr ask_slot
        bcs cancel
        jmp mount_sel

do_cd:   #control ctl_cd, CT_CD
        bcs _err
        lda #0
        sta page
        sta sel
        jmp reload_all
_err:   pha
        jsr draw_status
        pla
        jsr show_err
main2:  jmp main

cancel: jsr draw_status
        jmp main

; --- slots -------------------------------------------------------------------

; X = slot 0..3: mount the selected entry.
mount_sel:
        stx ctl_mnt_slot
        lda nent
        beq main2
        jsr set_eptr
        ldy #0
        lda (ptr),y
        and #$7F                ; bit7: read-only
        cmp #1
        beq main2               ; a directory cannot be mounted
        iny
        lda (ptr),y
        sta ctl_mnt_idx
        iny
        lda (ptr),y
        sta ctl_mnt_idx+1
        #control ctl_mnt, CT_MOUNT
        php
        pha
        jsr load_info
        jsr draw_title
        jsr draw_slots
        jsr draw_status
        pla
        plp
        bcs _err
        jsr msg_pos
        #print t_mounted
        lda ctl_mnt_slot
        jsr put_slotnum
        jmp main
_err:   jsr show_err
        jmp main

k_eject:
        jsr ask_slot
        bcs cancel
        stx ctl_one_val
        #control ctl_one, CT_EJECT
        php
        pha
        jsr load_info
        jsr draw_title
        jsr draw_slots
        jsr draw_status
        pla
        plp
        bcs _err
        jsr msg_pos
        #print t_ejected
        lda ctl_one_val
        jsr put_slotnum
        jmp main
_err:   jsr show_err
        jmp main

k_rotate:
        lda #0
        sta ctl_one_val
        #control ctl_one, CT_ROTATE
        php
        pha
        jsr load_info
        jsr draw_title
        jsr draw_slots
        jsr draw_status
        pla
        plp
        bcs _err
        lda #1                  ; Q is off from now on
        sta noquit
        jsr draw_help2
        jsr msg_pos
        #print t_rotated
        jmp main
_err:   jsr show_err
        jmp main

k_rescan:
        jsr draw_status
        jsr msg_pos
        #print t_reading
        lda #0
        sta ctl_one_val
        #control ctl_one, CT_RESCAN
        lda #0
        sta page
        sta sel
        jmp reload_all

k_nextdev:
        jsr find_next
        bcs _none
        lda #0
        sta page
        sta sel
        jmp reload_all
_none:  jsr msg_pos
        #print t_noother
        jsr clreol
        jmp main

k_boot: lda #0
        sta ctl_one_val
        #control ctl_one, CT_MENU
        jmp reboot

k_quit: lda noquit              ; menu boot, or after R: only B
        beq +
        jmp main
+       jmp quit

; Asks for a slot on the status line. Returns X = 0..3 and carry clear,
; or carry set if another key was pressed.
ask_slot:
        jsr msg_pos
        #print t_askslot
_key:   jsr getkey
        cmp #$B1
        bcc _no
        cmp #$B5
        bcs _no
        sbc #$B0                ; carry clear here: subtracts $B1
        tax
        clc
        rts
_no:    sec
        rts

; A = slot 0..3: print its number 1..4.
put_slotnum:
        clc
        adc #'1'
        jmp putc

; --- SmartPort -----------------------------------------------------------------

; Looks for the Core4SD: a SmartPort slot ($Cn01=$20, $Cn03=$00, $Cn05=$03,
; $Cn07=$00) with a unit whose device information block names "CORE4SD".
; Carry clear if found; sp_jsr then calls its dispatcher and sp_unit is set.
; The IIc and IIc Plus disk port is slot 5 with these id bytes.
find_dev:
        lda #$C7
        sta slotcn
_slot:  jsr try_slot
        bcc _found
        dec slotcn
        lda slotcn
        cmp #$C0
        bne _slot               ; slotcn = $C0: no device
        sec
_found: rts

; The next Core4SD below slot `slotcn`, going round from slot 7 to the slots
; above it (Tab). Carry set if there is no other: then the old one stays.
find_next:
        lda slotcn
        sta oldcn
        lda sp_jsr+1
        sta oldjsr
        lda sp_unit
        sta oldunit
        lda fwhi
        sta oldfwhi
        lda fwlo
        sta oldfwlo
_slot:  ldx slotcn
        dex
        cpx #$C0
        bne +
        ldx #$C7
+       stx slotcn
        cpx oldcn
        beq _none
        jsr try_slot
        bcs _slot
        rts
_none:  lda oldjsr
        sta sp_jsr+1
        lda oldcn
        sta sp_jsr+2
        lda oldunit
        sta sp_unit
        lda oldfwhi
        sta fwhi
        lda oldfwlo
        sta fwlo
        sec
        rts

; Slot `slotcn` with SmartPort id bytes and a Core4SD unit? Carry clear if
; so; sp_jsr then calls its dispatcher and sp_unit is set.
try_slot:
        lda #0
        sta ptr
        lda slotcn
        sta ptr+1
        ldy #1
        lda (ptr),y
        cmp #$20
        bne _no
        ldy #3
        lda (ptr),y
        bne _no
        ldy #5
        lda (ptr),y
        cmp #$03
        bne _no
        ldy #7
        lda (ptr),y
        bne _no
        ldy #$FF
        lda (ptr),y
        clc
        adc #3
        sta sp_jsr+1
        lda slotcn
        sta sp_jsr+2
        jmp find_unit
_no:    sec
        rts

; Asks the SmartPort at sp_jsr for its units and looks for one whose device
; information block names "CORE4SD". Carry clear and sp_unit set if found.
find_unit:
        lda #<DIBBUF
        sta sp_buf
        lda #>DIBBUF
        sta sp_buf+1
        lda #0                  ; unit 0, code 0: number of units
        sta sp_unit
        jsr sp_status
        bcs _none
        lda DIBBUF
        beq _none
        sta ndev
        lda #1
        sta sp_unit
_unit:  lda #3                  ; device information block
        jsr sp_status
        bcs _nextu
        ldx #0
_cmp:   lda DIBBUF+5,x
        cmp idstr,x
        bne _nextu
        inx
        cpx #idend-idstr
        bne _cmp
        lda DIBBUF+24           ; the firmware version (high, low)
        sta fwhi
        lda DIBBUF+23
        sta fwlo
        clc
        rts
_nextu: inc sp_unit
        lda ndev
        cmp sp_unit
        bcs _unit
_none:  sec
        rts

; STATUS (A = status code) or CONTROL (A = control code) on sp_unit with
; the buffer/list in sp_buf. Carry set and A = error code on failure.
sp_status:
        sta sp_code
        lda #$00
        beq sp_go
sp_control:
        sta sp_code
        lda #$04
sp_go:  sta sp_cmd
sp_jsr: jsr $0000               ; patched: SmartPort dispatcher
sp_cmd: .byte $00
        .word sp_parm
        rts

sp_parm:  .byte 3
sp_unit:  .byte 0
sp_buf:   .word 0
sp_code:  .byte 0

load_info:
        lda #<INFOBUF
        sta sp_buf
        lda #>INFOBUF
        sta sp_buf+1
        lda #ST_INFO
        jsr sp_status
        bcc _ok
        ldx #159
        lda #0
_clr:   sta INFOBUF,x
        dex
        bne _clr
        sta INFOBUF
_ok:    rts

load_page:
        lda #<PAGEBUF
        sta sp_buf
        lda #>PAGEBUF
        sta sp_buf+1
        lda page
        ora #ST_PAGE
        jsr sp_status
        bcc _ok
        lda #0
        sta PAGEBUF
        sta PAGEBUF+1
_ok:    lda PAGEBUF
        cmp #NENT+1
        bcc +
        lda #NENT
+       sta nent
        lda PAGEBUF+1
        and #1
        sta more
        lda sel                 ; keep the selection on the page
        cmp nent
        bcc _done
        ldx nent
        beq +
        dex
+       stx sel
_done:  rts

; ptr = entry `sel` of the page.
set_eptr:
        ldx sel
        lda eoff_lo,x
        sta ptr
        lda eoff_hi,x
        sta ptr+1
        rts

; --- screen --------------------------------------------------------------------

draw_all:
        jsr draw_title
        jsr draw_path
        jsr draw_list
        jsr draw_status
        jsr draw_slots
        ; fall through

; The key line; Quit only when there is a ProDOS to go back to.
draw_help2:
        ldx #0
        ldy #23
        jsr gotoxy
        lda #<t_help2
        ldy #>t_help2
        jsr putkeys
        lda #<t_boot1           ; "Boot 1", or "Reboot" when drive 1 is not
        ldy #>t_boot1           ; unit 1: the Apple then boots the device
        ldx sp_unit             ; in front of the Core4SD
        dex
        beq +
        lda #<t_reboot
        ldy #>t_reboot
+       jsr putkeys
        lda noquit
        bne +
        lda #<t_quit
        ldy #>t_quit
        jsr putkeys
+
        ldx #36
        jsr pad
        lda #<t_hint            ; "Help" at the right end
        ldy #>t_hint
        jsr putkeys
        jmp clreol

; Key line text at A/Y (low/high): the character after a "|" is the key,
; shown in inverse.
putkeys:
        sta ptr2
        sty ptr2+1
        lda #0
        sta sidx
_loop:  ldy sidx
        lda (ptr2),y
        beq _done
        cmp #'|'
        bne _put
        inc sidx
        ldy sidx
        lda #$80
        sta inv
        lda (ptr2),y
        jsr putc
        lda #0
        sta inv
        jmp _next
_put:   jsr putc
_next:  inc sidx
        bne _loop
_done:  rts

; --- help ----------------------------------------------------------------------

; ? or H: a window with the keys over the middle of the screen. The screen
; under it is saved first and put back after any key.
k_help: clc
        jsr help_copy           ; screen -> SAVEBUF
        jsr help_draw
        jsr getkey
        sec
        jsr help_copy           ; SAVEBUF -> screen
        jmp main

; Copies the window area of the screen to SAVEBUF (carry clear) or back
; (carry set).
help_copy:
        lda #0
        ror a
        sta hdir
        lda #<SAVEBUF
        sta ptr2
        lda #>SAVEBUF
        sta ptr2+1
        ldx #HELPY
_row:   lda lo_tab,x
        clc
        adc #HELPX
        sta ptr
        lda hi_tab,x
        adc #0
        sta ptr+1
        ldy #HELPW-1
_col:   bit hdir
        bmi _back
        lda (ptr),y
        sta (ptr2),y
        jmp _next
_back:  lda (ptr2),y
        sta (ptr),y
_next:  dey
        bpl _col
        lda ptr2
        clc
        adc #HELPW
        sta ptr2
        bcc +
        inc ptr2+1
+       inx
        cpx #HELPY+HELPH
        bcc _row
        rts

; Draws the window: the first and last lines of help_text in inverse, the
; others between an inverse left and right edge.
help_draw:
        lda #<help_text
        sta ptr2
        lda #>help_text
        sta ptr2+1
        lda #HELPY
        sta hrow
_line:  ldx #HELPX
        ldy hrow
        jsr gotoxy
        lda #$80
        sta inv
        lda hrow
        cmp #HELPY
        beq _full
        cmp #HELPY+HELPH-1
        beq _full
        lda #' '                ; left edge
        jsr putc
        lda #0
        sta inv
        jsr puts_next
        lda hrow                ; the first text line: version and model
        cmp #HELPY+1
        bne +
        jsr help_version
+       lda hrow                ; the B line: what B boots
        cmp #HELPY+HELP_BLINE
        bne +
        jsr help_boot
+       ldx #HELPX+HELPW-1
        jsr pad
        lda #$80
        sta inv
        lda #' '                ; right edge
        jsr putc
        jmp _nextl
_full:  jsr puts_next           ; top or bottom edge with its text
        ldx #HELPX+HELPW
        jsr pad
_nextl: inc hrow
        lda hrow
        cmp #HELPY+HELPH
        bcc _line
        lda #0
        sta inv
        rts

; The rest of the first line of the help window, "2.0 on A2ee": the firmware
; version from the device information block and the Apple model. Keeps ptr2
; (the place in help_text).
help_version:
        lda ptr2
        pha
        lda ptr2+1
        pha
        lda fwhi
        jsr putdec
        lda #'.'
        jsr putc
        lda fwlo
        jsr putdec
        #print t_on
        lda model
        sta ptr2
        lda model+1
        sta ptr2+1
        jsr puts
        pla
        sta ptr2+1
        pla
        sta ptr2
        rts

; The rest of the B line of the help window: "Boot drive 1", or "Reboot
; (unit 1)" when drive 1 is not unit 1. Keeps ptr2.
help_boot:
        lda ptr2
        pha
        lda ptr2+1
        pha
        ldx sp_unit
        dex
        bne +
        #print t_hboot1
        jmp _done
+       #print t_hreboot
_done:  pla
        sta ptr2+1
        pla
        sta ptr2
        rts

; A (0..255) in decimal.
putdec: sta num
        lda #0
        sta num+1
        sta num+2
        sta width
        jmp pnum

; Zero-terminated string at ptr2; ptr2 then points past its zero.
puts_next:
        jsr puts                ; sidx = its length
        lda sidx
        sec                     ; + 1 for the zero
        adc ptr2
        sta ptr2
        bcc +
        inc ptr2+1
+       rts

; The Apple model for the help window, from the monitor ROM id bytes (Apple II
; Miscellaneous Technical Note #7): model = pointer to its short name.
;   $FBB3 $38: II;  $EA: II+, or III in emulation ($FB1E = $8A);
;   $06: $FBC0 $EA IIe, $E0 IIe enhanced or IIgs (SEC / JSR $FE1F clears the
;   carry on a IIgs), $00 IIc, IIc Plus with $FBBF = $05.
; $FE1F is only called with $FBC0 = $E0: there it is an RTS or the IIgs id
; routine.
detect_model:
        ldx #<m_unk
        ldy #>m_unk
        lda $FBB3
        cmp #$38
        bne +
        ldx #<m_ii
        ldy #>m_ii
        bne _set                ; the high byte is never 0
+       cmp #$EA
        bne _iie
        ldx #<m_iip
        ldy #>m_iip
        lda $FB1E
        cmp #$8A
        bne _set
        ldx #<m_iii
        ldy #>m_iii
        bne _set
_iie:   cmp #$06
        bne _set
        lda $FBC0
        cmp #$EA
        bne +
        ldx #<m_iie
        ldy #>m_iie
        bne _set
+       cmp #$00
        bne _enh
        ldx #<m_iic
        ldy #>m_iic
        lda $FBBF
        cmp #$05
        bne _set
        ldx #<m_iicp
        ldy #>m_iicp
        bne _set
_enh:   cmp #$E0
        bne _set
        sec
        jsr $FE1F               ; IIgs: returns with the carry clear
        ldx #<m_iiee
        ldy #>m_iiee
        bcs _set
        ldx #<m_gs
        ldy #>m_gs
_set:   stx model
        sty model+1
        rts

draw_title:
        lda #$80
        sta inv
        ldx #0
        ldy #0
        jsr gotoxy
        #print t_title          ; " Core4SD"
        lda #' '                ; " 1.0": the firmware version
        jsr putc
        lda fwhi
        jsr putdec
        lda #'.'
        jsr putc
        lda fwlo
        jsr putdec
        #print t_slot           ; " - Slot 5, Units 1-4": the slot and the
        lda slotcn              ; units of drives 1-4 (2-5 and up when
        and #7                  ; another SmartPort device comes first in
        ora #'0'                ; the chain)
        jsr putc
        #print t_units
        lda sp_unit
        jsr putdec
        lda #'-'
        jsr putc
        lda sp_unit
        clc
        adc #3
        jsr putdec
        jsr clreol
        lda #0
        sta inv
        rts

draw_path:
        ldx #0
        ldy #1
        jsr gotoxy
        lda INFOBUF+1
        bpl _sd
        #print t_nosd
        jmp clreol
_sd:   #print t_sd
        lda INFOBUF             ; depth
        beq _end
        cmp #2
        bcc _one
        #print t_dots
_one:   lda #<(INFOBUF+2)
        sta ptr2
        lda #>(INFOBUF+2)
        sta ptr2+1
        jsr putname
        lda #'/'
        jsr putc
_end:   jmp clreol

draw_list:
        lda #0
        sta row
_loop:  jsr draw_entry
        inc row
        lda row
        cmp #NENT
        bcc _loop
        rts

; Draws entry `row` of the page (a blank line past the end).
draw_entry:
        lda row
        clc
        adc #LISTROW
        tay
        ldx #0
        jsr gotoxy
        lda #0
        sta inv
        lda row
        cmp nent
        bcc +
        jmp clreol
+       cmp sel
        bne +
        lda #$80
        sta inv
+       ldx row
        lda eoff_lo,x
        sta ptr
        clc
        adc #6
        sta ptr2
        lda eoff_hi,x
        sta ptr+1
        adc #0
        sta ptr2+1
        ldy #0
        lda (ptr),y
        and #$7F                ; the type; bit7 is read-only
        sta etype
        lda #' '
        ldx etype
        bne +
        lda #'-'                ; neither directory nor disk image
+       jsr putc
        lda #' '                ; the name in column 2, under the drive names
        jsr putc
        jsr putname
        lda etype
        cmp #1
        bne _file
        lda #'/'
        jsr putc
        ldx #31
        jsr pad
        #print t_dir
        jmp clreol
_file:  ldx #31                 ; size ends in column 38, as in the drive lines
        jsr pad
        ldy #3
        lda (ptr),y
        sta num
        iny
        lda (ptr),y
        sta num+1
        iny
        lda (ptr),y
        sta num+2
        lda #7
        sta width
        jsr pnum
        lda #'K'
        jsr putc
        ldy #0                  ; read-only file: '*' as in the drive lines
        lda (ptr),y
        bpl +
        lda #'*'
        jsr putc
+       jmp clreol

draw_status:
        lda #0
        sta inv
        ldx #0
        ldy #STATROW
        jsr gotoxy
        #print t_page
        ldx page
        inx
        stx num
        lda #0
        sta num+1
        sta num+2
        sta width
        jsr pnum
        lda more
        beq +
        #print t_more
+       jmp clreol

draw_slots:
        lda #0
        sta inv
        ldx #0                  ; "Drives ----" above the drive lines,
        ldy #SLOTROW-1          ; the rule up to the right edge
        jsr gotoxy
        #print t_drives
_rule:  lda col
        cmp #40
        bcs +
        lda #'-'
        jsr putc
        jmp _rule
+       lda #0
        sta row
_loop:  lda row
        clc
        adc #SLOTROW
        tay
        ldx #0
        jsr gotoxy
        lda row                 ; the drive number (normal; the key line
        jsr put_slotnum         ; shows 1-4 in inverse)
        lda #':'                ; "1:" and the name in column 2, as in the list
        jsr putc
        lda row                 ; ptr = INFOBUF + 30 + 32 * row
        asl a
        asl a
        asl a
        asl a
        asl a
        clc
        adc #<(INFOBUF+30)
        sta ptr
        lda #>(INFOBUF+30)
        adc #0
        sta ptr+1
        ldy #0
        lda (ptr),y
        and #1
        bne _mounted
        #print t_empty
        jmp _eol
_mounted:
        lda ptr
        clc
        adc #4
        sta ptr2
        lda ptr+1
        adc #0
        sta ptr2+1
        jsr putname
        ldx #31
        jsr pad
        ldy #2                  ; blocks / 2 = KB, rounded up like the
        lda (ptr),y             ; sizes in the list (1 block = 1K)
        lsr a
        sta num+1
        dey
        lda (ptr),y
        ror a
        sta num
        lda #0
        sta num+2
        bcc +                   ; odd number of blocks: one more K
        inc num
        bne +
        inc num+1
+       ldy #3                  ; + the KB of the file beyond its blocks
        lda (ptr),y             ; (a .2MG header): the file size, as in
        clc                     ; the list
        adc num
        sta num
        bcc +
        inc num+1
        bne +
        inc num+2
+
        lda #7
        sta width
        jsr pnum
        lda #'K'
        jsr putc
        ldy #0
        lda (ptr),y
        and #2
        beq _eol
        lda #'*'                ; write protected
        jsr putc
_eol:   jsr clreol
        inc row
        lda row
        cmp #4
        bcs +
        jmp _loop
+       rts

; Status line, message position.
msg_pos:
        ldx #12
        ldy #STATROW
        jsr gotoxy
        lda #0
        sta inv
        rts

; A = SmartPort error code.
show_err:
        pha
        jsr msg_pos
        #print t_error
        pla
        pha
        jsr phex
        pla
        cmp #$22
        bne +
        #print t_e22
        jmp clreol
+       cmp #$27
        bne +
        #print t_e27
        jmp clreol
+       cmp #$28
        bne +
        #print t_e28
+       jmp clreol

cls:    lda #0
        sta inv
        ldy #23
_row:   sty row
        ldx #0
        jsr gotoxy
        jsr clreol
        ldy row
        dey
        bpl _row
        rts

; X = column, Y = row.
gotoxy: stx col
        lda lo_tab,y
        sta scrp
        lda hi_tab,y
        sta scrp+1
        rts

; Writes the character in A at the cursor (ASCII, high bit ignored).
; Normal video with the high bit set; inverse (inv = $80) in the alternate
; character set: $00-$1F upper case, $20-$3F symbols, $60-$7F lower case.
; Only the 40 visible columns are written: the screen holes stay intact.
; On a II/II+ (upcase = $80) lower case letters become upper case.
putc:   and #$7F
        cmp #$20
        bcs +
        lda #' '
+       bit upcase
        bpl +
        cmp #'a'
        bcc +
        cmp #'z'+1
        bcs +
        and #$DF                ; a-z -> A-Z
+       bit inv
        bpl _norm
        cmp #$40
        bcc _put
        cmp #$60
        bcs _put
        and #$1F
        jmp _put
_norm:  ora #$80
_put:   ldy col
        cpy #40
        bcs _done
        sta (scrp),y
        inc col
_done:  rts

; Zero-terminated string at ptr2.
puts:   lda #0
        sta sidx
_loop:  ldy sidx
        lda (ptr2),y
        beq _done
        jsr putc
        inc sidx
        bne _loop
_done:  rts

; Name at ptr2: at most 28 characters, or up to a zero.
putname:
        lda #0
        sta sidx
_loop:  ldy sidx
        cpy #28
        bcs _done
        lda (ptr2),y
        beq _done
        jsr putc
        inc sidx
        bne _loop
_done:  rts

; Spaces up to column X.
pad:    stx padto
_loop:  lda col
        cmp padto
        bcs _done
        lda #' '
        jsr putc
        jmp _loop
_done:  rts

clreol: ldx #40
        jmp pad

; A as two hex digits.
phex:   pha
        lsr a
        lsr a
        lsr a
        lsr a
        jsr _nib
        pla
        and #$0F
_nib:   cmp #10
        bcc +
        adc #6
+       adc #'0'
        jmp putc

; num (24 bits) in decimal, right-aligned in `width` columns.
pnum:   ldx #0
_digit: jsr div10
        ora #'0'
        sta dbuf,x
        inx
        lda num
        ora num+1
        ora num+2
        bne _digit
        stx ndig
_pad:   cpx width
        bcs _out
        lda #' '
        jsr putc
        inx
        jmp _pad
_out:   ldx ndig
_put:   dex
        lda dbuf,x
        jsr putc
        cpx #0
        bne _put
        rts

; num /= 10, remainder in A. Keeps X.
div10:  lda #0
        ldy #24
_loop:  asl num
        rol num+1
        rol num+2
        rol a
        cmp #10
        bcc +
        sbc #10
        inc num
+       dey
        bne _loop
        rts

getkey: lda KBD
        bpl getkey
        sta KBDSTRB
        rts

; --- leaving -------------------------------------------------------------------

quit:   lda booted
        bne reboot
        lda MLI                 ; ProDOS there?
        cmp #$4C
        bne reboot
        jsr restore_screen
        jsr MLI
        .byte $65               ; QUIT
        .word quit_parm
reboot: jsr restore_screen
        lda #0
        sta ptr
        lda slotcn              ; boot the Core4SD's slot (PR#n)
        cmp #$C1
        bcs +
        lda #$C6                ; no device found: the disk slot
+       sta ptr+1
        jmp (ptr)

restore_screen:
        lda #0
        sta inv
        jsr cls
        sta CLRALTCH
        ldx #0
        ldy #0
        jmp gotoxy

quit_parm:
        .byte 4, 0
        .word 0
        .byte 0
        .word 0

; --- data --------------------------------------------------------------------

LINES   = [$400,$480,$500,$580,$600,$680,$700,$780, $428,$4A8,$528,$5A8,$628,$6A8,$728,$7A8, $450,$4D0,$550,$5D0,$650,$6D0,$750,$7D0]
lo_tab  .byte <LINES
hi_tab  .byte >LINES

ENTRIES = PAGEBUF + 2 + range(NENT) * ESIZE
eoff_lo .byte <ENTRIES
eoff_hi .byte >ENTRIES

idstr   .text "CORE4SD"         ; DIB name the firmware reports
idend

; Texts in mixed case: putc shows them in upper case on a II/II+.
t_search   .null "Searching Core4SD..."
t_nodev    .null "No Core4SD device found"
t_anykey   .null "Press any key."
t_hint     .null "|Help"             ; the key line, see putkeys
; The help window, one line each (HELPH lines, at most HELPW - 2 characters;
; the first and the last are the inverse top and bottom edge).
HELP_BLINE = 12                  ; the line of B in help_text (help_boot)
help_text  .null "               Help"    ; centred in HELPW
           .null "      Core4SD "         ; + version and model (help_version)
           .null ""
           .null " Up Dn  ^K ^J  Move selection"
           .null " , .  < >      Page up / down"
           .null " OA-Up OA-Dn   Page up / down"
           .null " <-  ESC       Parent directory"
           .null " ->  RETURN    Open / mount"
           .null " 1-4           Mount as drive"
           .null " E             Eject a drive"
           .null " R             Rotate the drives"
           .null " S             Re-read SD card"
           .null " B             "        ; + help_boot (line HELP_BLINE)
           .null " Q             Quit (ProDOS)"
           .null " Tab           Next Core4SD"
           .null " ?  H          This help"
           .null " In the list:"          ; a legend, not keys
           .null "   - = not a valid disk image"
           .null "   * = write protected"
           .null " Any key: back"
t_title    .null " Core4SD"
m_ii       .null "A2"           ; model names for the help window, see detect_model
m_iip      .null "A2+"
m_iii      .null "A3"
m_iie      .null "A2e"
m_iiee     .null "A2ee"
m_iic      .null "A2c"
m_iicp     .null "A2c+"
m_gs       .null "A2gs"
m_unk      .null "?"
t_slot     .null " - Slot "
t_units    .null ", Units "
t_nosd     .null "No SD card  (S = reload)"
t_sd       .null "SD:/"
t_dots     .null ".../"
t_help2    .null "|1|-|4 Mount |Eject |Rotate "  ; + t_boot1/t_reboot, t_quit, "Help"
t_boot1    .null "|Boot 1"
t_reboot   .null "Re|boot"           ; R is Rotate
t_quit     .null " |Quit"
t_hboot1   .null "Boot drive 1"
t_hreboot  .null "Reboot (unit 1)"
t_on       .null " on "
t_drives   .null "Drives "
t_dir      .null "   <DIR>"
t_page     .null "Page "
t_more     .null " >"
t_empty    .null "- empty -"
t_mounted  .null "Mounted as drive "
t_ejected  .null "Ejected: drive "
t_rotated  .null "Drives rotated"
t_noother  .null "No other Core4SD"
t_reading  .null "Reading SD card..."
t_askslot  .null "Which drive (1-4)? "
t_error    .null "Error $"
t_e22      .null " no image"
t_e27      .null " I/O"
t_e28      .null " no SD"

ctl_cd       .word 2
ctl_cd_idx   .word 0
ctl_mnt      .word 3
ctl_mnt_slot .byte 0
ctl_mnt_idx  .word 0
ctl_one      .word 1
ctl_one_val  .byte 0

booted  .byte 0
noquit  .byte 0                 ; 1: Q hidden and ignored (menu boot, or after R)
upcase  .byte 0                 ; $80: II/II+, lower case shown as upper case
model   .word m_unk             ; short name of the Apple model (detect_model)
fwhi    .byte 0                 ; firmware version from the DIB (find_unit)
fwlo    .byte 0
hdir    .byte 0                 ; help_copy: bit7 = back to the screen
hrow    .byte 0                 ; help_draw: screen row
slotcn  .byte 0
oldcn   .byte 0                 ; find_next: the Core4SD before Tab
oldjsr  .byte 0
oldunit .byte 0
oldfwhi .byte 0
oldfwlo .byte 0
ndev    .byte 0
page    .byte 0
sel     .byte 0
nent    .byte 0
more    .byte 0
row     .byte 0
col     .byte 0
inv     .byte 0
sidx    .byte 0
padto   .byte 0
etype   .byte 0
width   .byte 0
ndig    .byte 0
num     .fill 3, 0
dbuf    .fill 8, 0
