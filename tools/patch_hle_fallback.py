#!/usr/bin/env python3
"""patch_hle_fallback.py — amankan rc_hle_dispatch() + tambah telemetri fault.

LATAR BELAKANG
  recomp.cpp adalah kode game hasil rekompilasi (masuk ke build dari
  source_code.zip, TIDAK pernah di-commit ke repo ini — lihat job
  "forbidden-files" di CI). Fungsi rc_hle_dispatch() di sana adalah satu-
  satunya pintu masuk semua import PS Vita (svc #0, NID di r12).

  Skrip ini melakukan EMPAT patch independen (masing-masing idempotent):

    [FAULT_DBG_GLOBALS]  variabel telemetri fault (dibaca android_main.cpp
                         lewat extern "C") + rc_dbg_clear().
    [FAULT_DBG_FAULT]    fault() mencatat alamat/arah/pc akses memori gagal.
    [FAULT_DBG_UNIMPL]   rc_unimplemented() mencatat pc + deskripsi instruksi
                         yang belum diterjemahkan (penyebab RC_UNIMPL).
    [HLE_FALLBACK]       rc_hle_dispatch() tidak crash, mencetak HLE_MISSING
                         berisi NID + pc pemanggil + r0..r3, lalu return 0.

PEMAKAIAN
  python3 tools/patch_hle_fallback.py app/src/main/cpp/recomp.cpp

  Aman dijalankan berulang: setiap bagian yang sudah ada ditandai dan
  dilewati, jadi patch baru bisa ditambahkan tanpa merusak yang lama.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

GLOBALS_MARK = "[FAULT_DBG_GLOBALS]"
FAULT_MARK = "[FAULT_DBG_FAULT]"
UNIMPL_MARK = "[FAULT_DBG_UNIMPL]"
HLE_MARK = "[HLE_FALLBACK]"
LOADER_MARK = "[LOADER_PHDR_FIX]"
PROBE_MARK = "[ENTRY_PROBE]"
BLOCK_MISS_MARK = "[BLOCK_MISS]"
STEP_HIST_MARK = "[STEP_HIST]"

BLOCK_MISS_OLD = """    if (!b) {
        RC_LOGE("no recompiled block at 0x%08x (from 0x%08x)", next_pc, current_pc);
        return false;
    }"""

BLOCK_MISS_NEW = """    if (!b) {
        // [BLOCK_MISS] catat target yang tidak punya blok rekompilasi supaya
        // android_main.cpp bisa melaporkannya sebagai penyebab berhenti.
        rc_dbg_missing_pc = next_pc;
        rc_dbg_missing_from = current_pc;
        RC_LOGE("no recompiled block at 0x%08x (from 0x%08x)", next_pc, current_pc);
        // Mode debug: anggap fungsi tanpa blok sebagai stub yang return -1 lalu
        // kembali ke LR. Tanpa ini guest berhenti di stub pertama dan penyebab
        // yang sebenarnya (lubang translator / instruksi NEON) tidak terlihat.
        if (rc_dbg_stub_missing && r[kRegLr] != 0) {
            if (rc_dbg_stub_returns >= 1000000u) {
                RC_LOGE("batas %llu stub return tercapai di 0x%08x",
                        (unsigned long long)rc_dbg_stub_returns, next_pc);
                return false;
            }
            ++rc_dbg_stub_returns;
            r[0] = 0xFFFFFFFFu;
            next_pc = r[kRegLr] & ~1u;
            thumb = r[kRegLr] & 1u;
            cpsr = (cpsr & ~kFlagT) | (thumb ? kFlagT : 0);
            ++blocks_run;
            return true;
        }
        return false;
    }"""

STEP_BLOCK_BODY = r"""bool Cpu::step_block() {
    // [STEP_HIST] rekam PC blok yang akan dieksekusi (trace sebelum halt/fault).
    rc_dbg_hist_pc[rc_dbg_hist_i] = next_pc;
    rc_dbg_hist_i = (rc_dbg_hist_i + 1u) & 15u;
    if (rc_dbg_hist_n < 16u) ++rc_dbg_hist_n;
    ++rc_dbg_step_total;

    if (max_blocks && blocks_run >= max_blocks) { running = false; return false; }
    const BlockDesc* b = find_block(next_pc);
    if (!b) {
        // [BLOCK_MISS] catat target yang tidak punya blok rekompilasi supaya
        // android_main.cpp bisa melaporkannya sebagai penyebab berhenti.
        rc_dbg_missing_pc = next_pc;
        rc_dbg_missing_from = current_pc;
        RC_LOGE("no recompiled block at 0x%08x (from 0x%08x)", next_pc, current_pc);
        // Mode debug: anggap fungsi tanpa blok sebagai stub yang return -1 lalu
        // kembali ke LR. Tanpa ini guest berhenti di stub pertama dan penyebab
        // yang sebenarnya (lubang translator / instruksi NEON) tidak terlihat.
        if (rc_dbg_stub_missing && r[kRegLr] != 0) {
            if (rc_dbg_stub_returns >= 1000000u) {
                RC_LOGE("batas %llu stub return tercapai di 0x%08x",
                        (unsigned long long)rc_dbg_stub_returns, next_pc);
                return false;
            }
            ++rc_dbg_stub_returns;
            r[0] = 0xFFFFFFFFu;
            next_pc = r[kRegLr] & ~1u;
            thumb = r[kRegLr] & 1u;
            cpsr = (cpsr & ~kFlagT) | (thumb ? kFlagT : 0);
            ++blocks_run;
            return true;
        }
        return false;
    }
    current_pc = b->addr;
    thumb = b->thumb;
    cpsr = (cpsr & ~kFlagT) | (thumb ? kFlagT : 0);
    next_pc = b->addr;
    b->fn(*this);
    ++blocks_run;
    if (faulted) return false;
    if (next_pc == 0) { running = false; return false; }
    return true;
}"""

LOADER_OLD = """    uint32_t phoff, phentsize, phnum;
    std::memcpy(&phoff, eh + 28, 4);
    std::memcpy(&phentsize, eh + 42, 2);
    std::memcpy(&phnum, eh + 44, 2);"""

LOADER_NEW = """    // [LOADER_PHDR_FIX] e_phentsize/e_phnum adalah 16-bit. Dulu nilainya
    // di-memcpy ke uint32_t tanpa inisialisasi, jadi 2 byte atas berisi sampah
    // stack: loop membaca program header fiktif dan PT_LOAD asli bisa terlewat.
    uint32_t phoff = 0;
    uint16_t phentsize = 0, phnum = 0;
    std::memcpy(&phoff, eh + 28, 4);
    std::memcpy(&phentsize, eh + 42, 2);
    std::memcpy(&phnum, eh + 44, 2);
    RC_LOG("ELF phdr: phoff=%u phentsize=%u phnum=%u",
           phoff, (unsigned)phentsize, (unsigned)phnum);"""

PROBE_BLOCK = r'''
// [ENTRY_PROBE] Dipakai android_main.cpp untuk mencatat apakah entry yang
// dipilih benar-benar punya blok hasil rekompilasi (tanpa menjalankannya).
namespace rc {
bool rc_has_block(uint32_t pc) { return find_block(pc) != nullptr; }
}
extern "C" bool rc_dbg_has_block(uint32_t pc) { return rc::rc_has_block(pc); }
'''

GLOBALS_BLOCK = r'''
// [FAULT_DBG_GLOBALS] Telemetri fault guest. Ditambahkan otomatis oleh
// tools/patch_hle_fallback.py. android_main.cpp membacanya lewat extern "C"
// supaya log GUEST_FAULT bisa menyebutkan penyebab yang sebenarnya.
extern "C" {
uint32_t rc_dbg_fault_addr = 0;       // alamat akses memori yang gagal
uint32_t rc_dbg_fault_pc = 0;         // pc saat akses memori gagal
uint32_t rc_dbg_fault_write = 0;      // 1 = write, 0 = read
uint32_t rc_dbg_fault_seen = 0;       // 1 = fault() pernah dipanggil
uint32_t rc_dbg_unimpl_pc = 0;        // pc instruksi RC_UNIMPL
const char* rc_dbg_unimpl_what = "";  // deskripsi RC_UNIMPL ("invalid", ...)
uint32_t rc_dbg_last_pc = 0;          // pc pemanggil HLE terakhir
uint32_t rc_dbg_last_nid = 0;         // NID HLE terakhir
uint64_t rc_dbg_hle_calls = 0;        // total panggilan ke rc_hle_dispatch
uint32_t rc_dbg_missing_pc = 0;       // pc tanpa blok rekompilasi
uint32_t rc_dbg_missing_from = 0;     // pc pemanggil saat blok tidak ditemukan
uint32_t rc_dbg_stub_missing = 0;     // 1 = anggap fungsi tanpa blok sebagai stub -1
uint64_t rc_dbg_stub_returns = 0;     // jumlah stub return (tidak di-reset per chunk)

// Hook log opsional: diisi android_main.cpp supaya pesan HLE_MISSING /
// RC_UNIMPL juga masuk ke recomp.log (tab Log), bukan hanya logcat.
typedef void (*rc_dbg_log_fn)(const char* tag, const char* msg);
rc_dbg_log_fn rc_dbg_log_hook = nullptr;

// [STEP_HIST] ring buffer PC blok terakhir (untuk trace sebelum halt/fault).
uint32_t rc_dbg_hist_pc[16] = {0};
uint32_t rc_dbg_hist_n = 0;   // jumlah entri valid (<=16)
uint32_t rc_dbg_hist_i = 0;   // indeks tulis berikutnya
uint64_t rc_dbg_step_total = 0; // total step_block dieksekusi (debug trace)

// Caller (LR/return addr) terakhir untuk stub ARM tertentu (bantu cari game loop).
uint32_t rc_dbg_stub_caller_81815464 = 0;
uint32_t rc_dbg_stub_caller_81815494 = 0;

void rc_dbg_clear(void) {
    rc_dbg_fault_addr = 0;
    rc_dbg_fault_pc = 0;
    rc_dbg_fault_write = 0;
    rc_dbg_fault_seen = 0;
    rc_dbg_unimpl_pc = 0;
    rc_dbg_unimpl_what = "";
    rc_dbg_missing_pc = 0;
    rc_dbg_missing_from = 0;
}
}
'''

FAULT_BODY = r'''static void fault(Cpu& c, uint32_t addr, bool write) {
    // [FAULT_DBG_FAULT] catat akses memori yang gagal untuk GUEST_FAULT.
    rc_dbg_fault_addr = addr;
    rc_dbg_fault_pc = c.current_pc;
    rc_dbg_fault_write = write ? 1u : 0u;
    rc_dbg_fault_seen = 1u;
    if (!c.faulted) {
        RC_LOGE("guest memory fault: %s addr=0x%08x pc=0x%08x",
                write ? "write" : "read", addr, c.current_pc);
    }
    c.faulted = true;
}'''

UNIMPL_BODY = r'''void rc_unimplemented(uint32_t pc, const char* what) {
    // [FAULT_DBG_UNIMPL] catat instruksi yang belum diterjemahkan.
    rc_dbg_unimpl_pc = pc;
    rc_dbg_unimpl_what = what ? what : "?";
    RC_LOGE("guest unimplemented instruction: pc=0x%08x what=%s", pc, rc_dbg_unimpl_what);
    if (rc_dbg_log_hook) {
        char line[256];
        std::snprintf(line, sizeof(line),
                      "instruksi belum diterjemahkan di pc=0x%08x (%s)",
                      pc, rc_dbg_unimpl_what);
        rc_dbg_log_hook("RC_UNIMPL", line);
    }
}'''

HLE_BODY = r'''int rc_hle_dispatch(Cpu& cpu, uint32_t nid) {
    // [HLE_FALLBACK] Ditambahkan otomatis oleh tools/patch_hle_fallback.py.
    // Import yang sudah dipetakan tetap dipanggil seperti biasa.
    rc_dbg_last_pc = cpu.current_pc;
    rc_dbg_last_nid = nid;
    ++rc_dbg_hle_calls;

    for (size_t i = 0; i < rc_hle_count; ++i) {
        if (rc_hle_table[i].nid == nid) {
            if (!rc_hle_table[i].fn) break;
            return rc_hle_table[i].fn(cpu, cpu.r[0], cpu.r[1], cpu.r[2], cpu.r[3]);
        }
    }

    // NID belum dipetakan: JANGAN crash. Catat (maks 256 NID unik supaya
    // logcat/recomp.log tidak banjir) lalu kembalikan 0 sebagai nilai default.
    static uint32_t hle_seen_nids[256];
    static size_t hle_seen_count = 0;
    static uint64_t hle_missing_total = 0;
    ++hle_missing_total;

    bool known = false;
    for (size_t i = 0; i < hle_seen_count; ++i) {
        if (hle_seen_nids[i] == nid) { known = true; break; }
    }
    const bool first_time = (!known && hle_seen_count < 256);
    if (first_time) hle_seen_nids[hle_seen_count++] = nid;

#if defined(__ANDROID__) && !defined(RC_HOST_BUILD)
    if (first_time) {
        __android_log_print(ANDROID_LOG_ERROR, "HLE_MISSING",
            "%s import belum dipetakan: fungsi=\"unmapped\" (NID: 0x%08X) "
            "pemanggil_pc=0x%08X r0=0x%08X r1=0x%08X r2=0x%08X r3=0x%08X "
            "-> return 0",
            (hle_missing_total == 1) ? "PERTAMA" : "BARU",
            nid, cpu.current_pc,
            cpu.r[0], cpu.r[1], cpu.r[2], cpu.r[3]);
    }
    if (hle_missing_total == 1 || (hle_missing_total % 5000) == 0) {
        __android_log_print(ANDROID_LOG_ERROR, "HLE_MISSING",
            "Total import tak terpetakan: %llu panggilan, %zu NID unik "
            "(terakhir NID 0x%08X, pc=0x%08X)",
            (unsigned long long)hle_missing_total,
            (size_t)hle_seen_count, nid, cpu.current_pc);
    }
#else
    if (first_time) {
        std::fprintf(stderr,
            "HLE_MISSING: %s import belum dipetakan: fungsi=\"unmapped\" "
            "(NID: 0x%08X) pemanggil_pc=0x%08X r0=0x%08X r1=0x%08X "
            "r2=0x%08X r3=0x%08X -> return 0\n",
            (hle_missing_total == 1) ? "PERTAMA" : "BARU",
            nid, cpu.current_pc,
            cpu.r[0], cpu.r[1], cpu.r[2], cpu.r[3]);
    }
#endif

    // Salin ke recomp.log (tab Log) lewat hook host, bukan hanya logcat.
    if (rc_dbg_log_hook && (first_time || hle_missing_total == 1 || (hle_missing_total % 1000) == 0)) {
        char line[320];
        std::snprintf(line, sizeof(line),
            "import belum dipetakan: fungsi=\"unmapped\" (NID 0x%08X) "
            "pemanggil_pc=0x%08X r0=0x%08X r1=0x%08X r2=0x%08X r3=0x%08X "
            "-> return 0 (total=%llu unik=%zu)",
            nid, cpu.current_pc,
            cpu.r[0], cpu.r[1], cpu.r[2], cpu.r[3],
            (unsigned long long)hle_missing_total, hle_seen_count);
        rc_dbg_log_hook((hle_missing_total == 1) ? "HLE_MISSING[PERTAMA]"
                                                 : "HLE_MISSING", line);
    }
    return 0;
}
'''


def find_function_span(text: str, start: int) -> int:
    """Return index one past the closing brace of the function body at `start`."""
    depth = 0
    i = start
    in_str = None
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            i += 1
            continue
        if text.startswith("//", i):
            nl = text.find("\n", i)
            i = len(text) if nl == -1 else nl + 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end == -1 else end + 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def replace_span(text: str, signature: re.Pattern[str], body: str, what: str):
    """Replace a whole function definition matched by `signature` with `body`."""
    m = signature.search(text)
    if not m:
        return text, False
    end = find_function_span(text, m.end() - 1)
    if end == -1:
        raise RuntimeError(f"tidak bisa menemukan akhir fungsi {what}")
    return text[: m.start()] + body + text[end:], True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("pemakaian: patch_hle_fallback.py <path/ke/recomp.cpp>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    if not path.is_file():
        print(f"error: {path} tidak ditemukan", file=sys.stderr)
        return 1

    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    changed: list[str] = []

    # 1) Variabel telemetri global (di depan namespace rc).
    if GLOBALS_MARK not in text:
        marker = "namespace rc {"
        idx = text.find(marker)
        if idx == -1:
            print(f"error: 'namespace rc {{' tidak ditemukan di {path}", file=sys.stderr)
            return 1
        text = text[:idx] + GLOBALS_BLOCK + "\n" + text[idx:]
        changed.append("globals")

    # 2) fault() -> catat alamat/arah/pc.
    if FAULT_MARK not in text:
        sig = re.compile(
            r"static\s+void\s+fault\s*\(\s*Cpu\s*&\s*c\s*,\s*uint32_t\s+addr\s*,"
            r"\s*bool\s+write\s*\)\s*\{"
        )
        text, done = replace_span(text, sig, FAULT_BODY, "fault()")
        if not done:
            print(f"error: fault() tidak ditemukan di {path}", file=sys.stderr)
            return 1
        changed.append("fault")

    # 3) rc_unimplemented() -> catat pc/what.
    if UNIMPL_MARK not in text:
        sig = re.compile(
            r"void\s+rc_unimplemented\s*\(\s*uint32_t\s+pc\s*,\s*"
            r"const\s+char\s*\*\s*what\s*\)\s*\{"
        )
        text, done = replace_span(text, sig, UNIMPL_BODY, "rc_unimplemented()")
        if not done:
            print(f"error: rc_unimplemented() tidak ditemukan di {path}", file=sys.stderr)
            return 1
        changed.append("unimplemented")

    # 4) rc_hle_dispatch() -> HLE_MISSING + return 0.
    if HLE_MARK not in text:
        sig = re.compile(
            r"int\s+rc_hle_dispatch\s*\(\s*Cpu\s*&\s*cpu\s*,\s*uint32_t\s+nid\s*\)\s*\{"
        )
        text, done = replace_span(text, sig, HLE_BODY, "rc_hle_dispatch()")
        if not done:
            print(f"error: rc_hle_dispatch() tidak ditemukan di {path}", file=sys.stderr)
            return 1
        changed.append("hle")

    # 5) Loader ELF: perbaiki tipe e_phentsize/e_phnum (uninitialized memcpy).
    if LOADER_MARK not in text:
        if LOADER_OLD not in text:
            print(f"error: blok pembaca program header tidak ditemukan di {path}",
                  file=sys.stderr)
            return 1
        text = text.replace(LOADER_OLD, LOADER_NEW, 1)
        changed.append("loader")

    # 6) Probe blok: rc_dbg_has_block() untuk log GUEST_ENTRY.
    if PROBE_MARK not in text:
        text = text.rstrip() + "\n" + PROBE_BLOCK
        changed.append("probe")

    # 7) step_block(): catat pc target yang tidak punya blok.
    if BLOCK_MISS_MARK not in text:
        if BLOCK_MISS_OLD not in text:
            print(f"error: badan 'if (!b)' step_block() tidak ditemukan di {path}",
                  file=sys.stderr)
            return 1
        text = text.replace(BLOCK_MISS_OLD, BLOCK_MISS_NEW, 1)
        changed.append("block_miss")

    # 8) step_block(): rekam history PC blok (trace sebelum halt/fault).
    if STEP_HIST_MARK not in text:
        sig = re.compile(r"bool\s+Cpu::step_block\s*\(\s*\)\s*\{")
        text, done = replace_span(text, sig, STEP_BLOCK_BODY, "Cpu::step_block()")
        if not done:
            print(f"error: Cpu::step_block() tidak ditemukan di {path}", file=sys.stderr)
            return 1
        changed.append("step_hist")

    if not changed:
        print(f"OK: {path} sudah dipatch penuh — tidak diubah.")
        return 0

    # Sanity check: semua penanda harus ada setelah patch.
    missing = [m for m in (GLOBALS_MARK, FAULT_MARK, UNIMPL_MARK, HLE_MARK,
                           LOADER_MARK, PROBE_MARK, BLOCK_MISS_MARK, STEP_HIST_MARK)
               if m not in text]
    if missing:
        print(f"error: penanda hilang setelah patch: {missing}", file=sys.stderr)
        return 1

    path.write_text(text, encoding="utf-8", errors="surrogateescape")
    print(f"OK: {path} dipatch ({', '.join(changed)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
