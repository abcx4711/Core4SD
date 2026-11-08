/*
 * host_sim.cpp -- the real firmware (main.cpp) compiled for the host, with
 * mocked AVR ports, EEPROM and SdFat, driven packet by packet from Python
 * (test/sim_e2e.py) through a small C API.
 */
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <algorithm>
#include <ctype.h>
#include "SdFat.h"
#include "Arduino.h"

uint8_t PORTD, DDRD, PIND, PINC, ACSR;
AckReg PORTC, DDRC;
int mock_ack_lows;

static bool ack_driven_low(void) { return (DDRC.v & 0x20) && !(PORTC.v & 0x20); }

AckReg &AckReg::operator=(int x)
{
    bool before = ack_driven_low();
    v = (uint8_t)x;
    if (!before && ack_driven_low())
        mock_ack_lows++;
    return *this;
}
uint8_t mock_eeprom[1024];
int mock_eeprom_writes;
std::string mock_sd_root;
bool mock_sd_present = true;
static int mock_button;

void pinMode(uint8_t, uint8_t) {}
void digitalWrite(uint8_t, uint8_t) {}
int digitalRead(uint8_t pin) { return pin == 17 ? mock_button : 0; }
void delay(unsigned long) {}

/* ---- bus ---------------------------------------------------------------- */

static uint8_t tx_buf[700];
static int tx_len;
static uint8_t rx_queue[700];
static int rx_queued;
static int tx_count;

extern "C" unsigned char SendPacket(unsigned char *buf)
{
    int n = 0;
    while (buf[n]) n++;
    memcpy(tx_buf, buf, n + 1);
    tx_len = n;
    tx_count++;
    return 0;
}

extern "C" unsigned char ReceivePacket(unsigned char *buf, uint16_t size, uint16_t *received)
{
    if (!rx_queued) { fprintf(stderr, "ReceivePacket: nothing queued\n"); abort(); }
    memcpy(buf, rx_queue, size < 604 ? size : 604);
    rx_queued = 0;
    if (received) *received = strlen((const char *)buf);
    return 0;
}

/* ---- SdFat mock ------------------------------------------------------------ */

/* One case per part (base, extension), like the FAT case flags allow. */
static bool one_case(const std::string &p)
{
    bool lo = false, up = false;
    for (char c : p) { lo |= !!islower((unsigned char)c); up |= !!isupper((unsigned char)c); }
    return !(lo && up);
}

static bool is_83(const std::string &n)
{
    size_t dot = n.find('.');
    if (n.find('.', dot == std::string::npos ? n.size() : dot + 1) != std::string::npos) return false;
    std::string base = n.substr(0, dot), ext = dot == std::string::npos ? "" : n.substr(dot + 1);
    if (base.empty() || base.size() > 8 || ext.size() > 3) return false;
    for (char c : n) if (c == ' ' || c == '+' || c == ',') return false;
    return one_case(base) && one_case(ext);
}

static std::string upper(std::string s)
{
    for (auto &c : s) c = toupper((unsigned char)c);
    return s;
}

std::vector<MockEntry> FatFile::list(const std::string &path)
{
    std::vector<MockEntry> v;
    DIR *d = opendir(path.c_str());
    if (!d) return v;
    struct dirent *de;
    while ((de = readdir(d))) {
        std::string n = de->d_name;
        if (n == "." || n == "..") continue;
        MockEntry e;
        struct stat st;
        e.name = n;
        e.path = path + "/" + n;
        stat(e.path.c_str(), &st);
        e.dir = S_ISDIR(st.st_mode);
        e.size = e.dir ? 0 : (uint32_t)st.st_size;
        e.hidden = n.compare(0, 7, "_hidden") == 0;
        e.ro = n.find("_ro") != std::string::npos;
        v.push_back(e);
    }
    closedir(d);
    std::sort(v.begin(), v.end(), [](const MockEntry &a, const MockEntry &b) { return a.name < b.name; });
    uint16_t pos = 0;
    int tilde = 1;
    for (auto &e : v) {
        if (is_83(e.name)) {
            e.sfn = e.name;
            e.index = pos;
        } else {
            std::string n = e.name, base, ext;
            size_t dot = n.rfind('.');
            base = n.substr(0, dot);
            ext = dot == std::string::npos ? "" : n.substr(dot + 1, 3);
            std::string b;
            for (char c : base) if (c != ' ' && c != '.') b += toupper((unsigned char)c);
            e.sfn = b.substr(0, 6) + "~" + std::to_string(tilde++) + (ext.empty() ? "" : "." + upper(ext));
            e.index = pos + (uint16_t)((n.size() + 12) / 13);  /* LFN entries in front */
        }
        pos = e.index + 2;                                    /* plus a deleted slot */
    }
    return v;
}

void FatFile::setDir(const std::string &path, const std::string &name, uint16_t index)
{
    m_open = true; m_dir = true; m_fp = 0;
    m_path = path; m_name = name; m_index = index;
    m_pos = 0; m_size = 0; m_hidden = false; m_ro = false;
}

bool FatFile::openEntry(const MockEntry &e, oflag_t oflag)
{
    if (e.dir) {
        setDir(e.path, e.name, e.index);
        m_sfn = e.sfn;
        return true;
    }
    if (oflag == O_RDWR && e.ro) return false;
    m_fp = fopen(e.path.c_str(), oflag == O_RDWR ? "r+b" : "rb");
    if (!m_fp) return false;
    m_open = true; m_dir = false;
    m_path = e.path; m_name = e.name; m_sfn = e.sfn; m_index = e.index;
    m_pos = 0; m_size = e.size; m_hidden = e.hidden; m_ro = e.ro;
    return true;
}

bool FatFile::open(FatFile *dir, uint16_t index, oflag_t oflag)
{
    if (m_open || !dir || !dir->isDir()) return false;
    for (auto &e : list(dir->m_path))
        if (e.index == index) { dir->m_pos = (index + 1) * 32; return openEntry(e, oflag); }
    return false;
}

bool FatFile::open(FatVolume *, const char *path, oflag_t oflag)
{
    if (m_open || *path != '/') return false;
    std::string cur = mock_sd_root, p = path + 1;
    while (!p.empty()) {
        size_t sl = p.find('/');
        std::string comp = upper(p.substr(0, sl));
        p = sl == std::string::npos ? "" : p.substr(sl + 1);
        bool found = false;
        for (auto &e : list(cur)) {
            if (upper(e.sfn) == comp) {
                if (p.empty()) {
                    /* like SdFat: found by its 8.3 name, the file does not
                     * know its long name (m_lfnOrd = 0), getName() gives
                     * the 8.3 name */
                    if (!openEntry(e, oflag)) return false;
                    m_name = m_sfn;
                    return true;
                }
                if (!e.dir) return false;
                cur = e.path;
                found = true;
                break;
            }
        }
        if (!found) return false;
    }
    return false;
}

bool FatFile::openNext(FatFile *dir, oflag_t oflag)
{
    if (m_open || !dir || !dir->isDir() || (dir->m_pos & 31)) return false;
    for (auto &e : list(dir->m_path)) {
        if ((uint32_t)e.index * 32 >= dir->m_pos) {
            dir->m_pos = ((uint32_t)e.index + 1) * 32;
            return openEntry(e, oflag);
        }
    }
    dir->m_pos += 32;   /* reached the free entry at the end */
    return false;
}

bool FatFile::close()
{
    if (m_fp) fclose(m_fp);
    m_fp = 0;
    m_open = false;
    return true;
}

size_t FatFile::getName(char *name, size_t size)
{
    /* SdFat 2 (getName7): the whole name or nothing */
    if (!m_open || m_name.size() + 1 > size) { if (size) name[0] = 0; return 0; }
    memcpy(name, m_name.c_str(), m_name.size() + 1);
    return m_name.size();
}

size_t FatFile::getSFN(char *name, size_t size)
{
    if (!m_open || m_sfn.size() + 1 > size) { if (size) name[0] = 0; return 0; }
    memcpy(name, m_sfn.c_str(), m_sfn.size() + 1);
    return m_sfn.size();
}

bool FatFile::seekSet(uint32_t pos)
{
    if (!m_open) return false;
    if (!m_dir && pos > m_size) return false;
    m_pos = pos;
    if (m_fp) fseek(m_fp, pos, SEEK_SET);
    return true;
}

int FatFile::read(void *buf, size_t n)
{
    if (!m_fp) return -1;
    int r = (int)fread(buf, 1, n, m_fp);
    m_pos += r;
    return r;
}

int FatFile::write(const void *buf, size_t n)
{
    if (!m_fp) return -1;
    int r = (int)fwrite(buf, 1, n, m_fp);
    m_pos += r;
    return r;
}

bool FatFile::sync()
{
    if (m_fp) fflush(m_fp);
    return true;
}

/* ---- the firmware itself ---------------------------------------------------- */

#include "../src/main.cpp"

/* ---- C API for Python -------------------------------------------------------- */

extern "C" {

void sim_setup(const char *root, int button, int sd_present)
{
    for (int s = 0; s < NUM_SLOTS; s++) { slots[s].f.close(); slots[s].flags = 0; slots[s].id = 0; }
    cwd.close();
    n_init = 0;
    mock_sd_root = root;
    mock_button = button;
    mock_sd_present = sd_present;
    setup();
}

/* Closes every open file (Windows cannot delete the test card otherwise). */
void sim_close(void)
{
    for (int s = 0; s < NUM_SLOTS; s++) { slots[s].f.close(); slots[s].flags = 0; }
    cwd.close();
}

void sim_bus_reset(void)
{
    n_init = 0;
    for (int s = 0; s < NUM_SLOTS; s++) slots[s].id = 0;
}

void sim_eeprom_clear(void) { memset(mock_eeprom, 0xFF, sizeof mock_eeprom); }
const uint8_t *sim_eeprom(void) { return mock_eeprom; }
int sim_eeprom_writes(void) { return mock_eeprom_writes; }
void sim_set_button(int b) { mock_button = b; }
void sim_set_sd(int p) { mock_sd_present = p; }   /* insert or pull the card */
int sim_ack_lows(void) { return mock_ack_lows; }  /* ACK pulled low in the last sim_packet */
int sim_ack_driven(void) { return DDRC.v & 0x20 ? 1 : 0; }

/* One exchange: `cmd` is a received command packet (rx layout), `data` an
 * optional data packet the host sends right after it. The packet the
 * firmware answers with is copied to `tx`; returns its length (0 = none). */
int sim_packet(const uint8_t *cmd, int cmdlen, const uint8_t *data, int datalen, uint8_t *tx)
{
    memset(pb, 0xEE, sizeof pb);
    memcpy(pb, cmd, cmdlen);
    if (data) { memset(rx_queue, 0xEE, sizeof rx_queue); memcpy(rx_queue, data, datalen); rx_queued = 1; }
    tx_len = 0;
    int before = tx_count;
    PORTC |= 0x20;                  /* as loop() leaves it: ACK high, driven */
    DDRC |= 0x20;
    mock_ack_lows = 0;
    process_packet();
    if (rx_queued) { fprintf(stderr, "data packet not consumed\n"); abort(); }
    if (tx_count == before) return 0;
    memcpy(tx, tx_buf, tx_len + 1);
    return tx_len;
}

}
