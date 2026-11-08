/*
 * Host mock of SdFat 2.3's FatFile/SdFat over a host directory (test only).
 *
 * Mimics what the firmware relies on: directory entry indices with room for
 * LFN entries in front of each short entry, openNext() leaving the directory
 * positioned after the entry, open by index, 8.3 names and 8.3 paths,
 * getName() that fails when the buffer is too small, getSFN() that keeps
 * the case of lower-case 8.3 names, move-only files. Names starting with "_hidden" get the hidden
 * attribute; names containing "_ro" are read-only.
 */
#ifndef MOCK_SDFAT_H
#define MOCK_SDFAT_H

#include <stdint.h>
#include <stdio.h>
#include <string>
#include <vector>

typedef int oflag_t;
#define O_RDONLY 0
#define O_RDWR   2
#define DEDICATED_SPI 1
#define SD_SCK_MHZ(x) (x)
struct SdSpiConfig { SdSpiConfig(int, int, int) {} };

class FatVolume {};

extern std::string mock_sd_root;
extern bool mock_sd_present;

class SdFat : public FatVolume {
public:
    bool begin(SdSpiConfig) { return mock_sd_present; }
};

struct MockEntry {
    std::string name, sfn, path;
    bool dir, hidden, ro;
    uint32_t size;
    uint16_t index;
};

class FatFile {
public:
    FatFile() : m_open(false), m_dir(false), m_fp(0), m_pos(0), m_size(0), m_index(0), m_hidden(false), m_ro(false) {}
    FatFile(const FatFile &) = delete;
    FatFile &operator=(const FatFile &) = delete;
    FatFile &operator=(FatFile &&o) {
        if (&o != this) {
            m_open = o.m_open; m_dir = o.m_dir; m_fp = o.m_fp;
            m_path = o.m_path; m_name = o.m_name; m_sfn = o.m_sfn;
            m_pos = o.m_pos; m_size = o.m_size; m_index = o.m_index; m_hidden = o.m_hidden; m_ro = o.m_ro;
            o.m_open = false; o.m_fp = 0;
        }
        return *this;
    }

    bool isOpen() const { return m_open; }
    bool isDir() const { return m_open && m_dir; }
    bool isHidden() const { return m_hidden; }
    bool isReadOnly() const { return m_ro; }
    bool isSystem() const { return false; }
    uint16_t dirIndex() { return m_index; }
    uint32_t fileSize() const { return m_size; }
    uint32_t curPosition() const { return m_pos; }

    bool openRoot(FatVolume *) {
        if (m_open) return false;
        setDir(mock_sd_root, "", 0);
        return true;
    }
    bool open(FatFile *dir, uint16_t index, oflag_t oflag);
    bool open(FatVolume *vol, const char *path, oflag_t oflag);
    bool openNext(FatFile *dir, oflag_t oflag);
    bool close();
    size_t getName(char *name, size_t size);
    size_t getSFN(char *name, size_t size);
    bool seekSet(uint32_t pos);
    void rewind() { seekSet(0); }
    int read(void *buf, size_t n);
    int write(const void *buf, size_t n);
    bool sync();

    static std::vector<MockEntry> list(const std::string &path);

private:
    void setDir(const std::string &path, const std::string &name, uint16_t index);
    bool openEntry(const MockEntry &e, oflag_t oflag);

    bool m_open, m_dir;
    FILE *m_fp;
    std::string m_path, m_name, m_sfn;
    uint32_t m_pos, m_size;
    uint16_t m_index;
    bool m_hidden, m_ro;
};

#endif
