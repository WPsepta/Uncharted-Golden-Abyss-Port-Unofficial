#!/usr/bin/env python3
"""Ubah `bkpt` (breakpoint ARM) yang belum diterjemahkan jadi no-op.

BKPT adalah instruksi debugger/trap; dalam emulator tidak ada debugger yang
menangkapnya, jadi aman dilompati.  Translator menghasilkan `RC_UNIMPL(...)`
untuk instruksi ini sehingga guest fault.

Aturan:
  * Jika `bkpt` adalah instruksi TERAKHIR di blok (tidak ada next_pc / RC_FALL
    / akses register setelahnya), ganti dengan `c.next_pc = addr+2; return;`
    supaya guest lanjut ke instruksi berikutnya.
  * Jika ada instruksi sesudahnya dalam blok yang sama, cukup hapus baris
    RC_UNIMPL-nya (alon-alon, eksekusi berlanjut ke instruksi berikutnya).

Dijalankan paling akhir (setelah regen & rewrite lain) agar semua part termasuk
part998 ter-coverage.

Pemakaian:
    python3 tools/patch_bkpt.py <app/src/main/cpp>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PAT = re.compile(
    r'^(?P<indent>[ \t]*)RC_UNIMPL\((?P<addr>0x(?P<addrhex>[0-9a-f]+)u), "(?P<what>bkpt[^"]*)"\)(?P<tail>.*)$'
)
# Baris yang masih merupakan "kode setelah bkpt" dalam blok yang sama.
LATER = re.compile(
    r'\bc\.next_pc\s*=|\bRC_FALL\b|\bc\.r\[|\bc\.read|\bc\.write|'
    r'\bRC_(ADD|SUB|LOGIC|SET|MUL|LDR|STR)|^[^/]*\breturn\b'
)


def rewrite_file(path: Path) -> int:
    try:
        src = path.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return 0
    lines = src.splitlines(keepends=True)
    changed = 0
    # Kumpulkan indeks baris bkpt.
    hits: list[tuple[int, re.Match, str]] = []
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        m = PAT.match(body)
        if m:
            hits.append((i, m, line[len(body):]))  # simpan ending newline
    if not hits:
        return 0

    #Untuk setiap bkpt, cek apakah ada kode sesudahnya sebelum akhir fungsi.
    for i, m, ending in hits:
        indent = m.group("indent")
        addr = m.group("addr")
        val = int(m.group("addrhex"), 16)
        nxt = val + 2
        has_later = False
        for j in range(i + 1, len(lines)):
            s = lines[j]
            if s.startswith("}"):
                break
            code = s.strip()
            if not code or code.startswith("//"):
                continue
            if LATER.search(s):
                has_later = True
            break
        if has_later:
            repl = f"{indent}// [BKPT_NOP] {m.group('what')} {addr} -> diabaikan\n"
        else:
            repl = (
                f"{indent}// [BKPT_NOP] {m.group('what')} {addr} -> lanjut ke 0x{nxt:08x}u\n"
                f"{indent}c.next_pc = 0x{nxt:08x}u; return;\n"
            )
        lines[i] = repl
        changed += 1

    path.write_text("".join(lines), encoding="utf-8", errors="surrogateescape")
    return changed


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) != 1:
        print("pemakaian: patch_bkpt.py <app/src/main/cpp>", file=sys.stderr)
        return 2
    gen = Path(args[0]) / "gen"
    if not gen.is_dir():
        print(f"error: {gen} tidak ditemukan", file=sys.stderr)
        return 1
    total = 0
    files = 0
    for cpp in sorted(gen.glob("recompiled_part*.cpp")):
        if cpp.name == "recompiled_part999.cpp":
            continue
        n = rewrite_file(cpp)
        if n:
            files += 1
            total += n
    print(f"OK: {total} bkpt di-rewrite jadi no-op di {files} file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
