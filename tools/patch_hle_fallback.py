#!/usr/bin/env python3
"""patch_hle_fallback.py — amankan rc_hle_dispatch() di recomp.cpp.

LATAR BELAKANG
  recomp.cpp adalah kode game hasil rekompilasi (masuk ke build dari
  source_code.zip, TIDAK pernah di-commit ke repo ini — lihat job
  "forbidden-files" di CI). Fungsi rc_hle_dispatch() di sana adalah satu-
  satunya pintu masuk semua import PS Vita (svc #0, NID di r12).

  Versi asli hanya mencetak "unmapped HLE import nid=..." lalu return -1.
  Untuk eksperimen boot ELF, kita ingin:
    1. tidak pernah crash karena NID tak dikenal,
    2. log HLE_MISSING yang jelas (NID, pc pemanggil, total),
    3. return 0 (nilai default) supaya eksekusi guest bisa lanjut.

PEMAKAIAN
  python3 tools/patch_hle_fallback.py app/src/main/cpp/recomp.cpp

  Aman dijalankan berulang (idempotent): jika sudah ada "HLE_MISSING",
  skrip hanya melaporkan bahwa file sudah dipatch.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SIGNATURE = re.compile(
    r"int\s+rc_hle_dispatch\s*\(\s*Cpu\s*&\s*cpu\s*,\s*uint32_t\s+nid\s*\)\s*\{"
)

PATCHED_BODY = r'''int rc_hle_dispatch(Cpu& cpu, uint32_t nid) {
    // [HLE_FALLBACK] Ditambahkan otomatis oleh tools/patch_hle_fallback.py.
    // Import yang sudah dipetakan tetap dipanggil seperti biasa.
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
            "Unmapped HLE import: %s (NID: 0x%08X) pc=0x%08X -> return 0",
            "unmapped", nid, cpu.current_pc);
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
            "HLE_MISSING: Unmapped HLE import: %s (NID: 0x%08X) pc=0x%08X -> return 0\n",
            "unmapped", nid, cpu.current_pc);
    }
#endif
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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("pemakaian: patch_hle_fallback.py <path/ke/recomp.cpp>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    if not path.is_file():
        print(f"error: {path} tidak ditemukan", file=sys.stderr)
        return 1

    text = path.read_text(encoding="utf-8", errors="surrogateescape")

    if "HLE_MISSING" in text and "[HLE_FALLBACK]" in text:
        print(f"OK: {path} sudah dipatch (HLE_MISSING sudah ada) — tidak diubah.")
        return 0

    m = SIGNATURE.search(text)
    if not m:
        print(f"error: rc_hle_dispatch tidak ditemukan di {path}", file=sys.stderr)
        return 1

    end = find_function_span(text, m.end() - 1)
    if end == -1:
        print(f"error: tidak bisa menemukan akhir fungsi di {path}", file=sys.stderr)
        return 1

    patched = text[: m.start()] + PATCHED_BODY + text[end:]

    if "HLE_MISSING" not in patched:
        print("error: hasil patch tidak berisi HLE_MISSING", file=sys.stderr)
        return 1

    path.write_text(patched, encoding="utf-8", errors="surrogateescape")
    print(f"OK: {path} dipatch — rc_hle_dispatch kini log HLE_MISSING + return 0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
