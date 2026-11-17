#!/usr/bin/env python3
"""Rewrite `mrc p15, 0, rX, c13, c0, 3` (TLS-get) yang belum diterjemahkan.

Translator tidak punya aturan untuk `mrc p15, <opc1>, rX, c13, c0, 3`
(pembacaan register TLS TPIDRURO), sehingga baris tersebut menjadi
`RC_UNIMPL(...)` dan guest fault.  Instruksi ini bukan HLE / bukan akses
memori — cukup diisi register tujuan dengan basis TLS guest.

Patch ini dijalankan PALING AKHIR (setelah regen_invalid_region.py) karena
script itu menimpa `recompiled_part998.cpp`; dengan demikian semua part
termasuk part998 ter-coverage.

Pemakaian:
    python3 tools/patch_mrc_c13.py <app/src/main/cpp>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Basis TLS guest.  Sama dengan nilai pada fix manual 0x810a61ec; berada di
# dalam window guest [0x81000000, 0x8fffffff) sehingga aman untuk dibaca.
TLS_PTR = 0x81970000

# Bentuk persis yang dihasilkan translator.
PAT = re.compile(
    r'^(?P<indent>[ \t]*)RC_UNIMPL\((?P<addr>0x[0-9a-f]+u), '
    r'"mrc p15, 0, (?P<reg>r\d+), c13, c0, 3"\)(?P<tail>.*)$'
)


def rewrite_file(path: Path) -> int:
    try:
        src = path.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return 0

    out_lines: list[str] = []
    changed = 0
    for line in src.splitlines(keepends=True):
        # splitlines(keepends=True) menyertakan '\n'; lepas untuk cocokkan ^
        nl = ""
        body = line
        if body.endswith("\r\n"):
            body, nl = body[:-2], "\r\n"
        elif body.endswith("\n"):
            body, nl = body[:-1], "\n"
        m = PAT.match(body)
        if not m:
            out_lines.append(line)
            continue
        idx = int(m.group("reg")[1:])
        indent = m.group("indent")
        addr = m.group("addr")
        tail = m.group("tail")
        repl = (
            f'{indent}c.r[{idx}] = 0x{TLS_PTR:08x}u;'
            f'  // [MRC_C13_TLS] {addr} r{idx} = TLS base'
        )
        # pertahankan komentar "// mrc" di akhir bila ada
        if "//" in tail:
            repl += " " + tail[tail.index("//"):]
        out_lines.append(repl + nl)
        changed += 1

    if changed:
        path.write_text("".join(out_lines), encoding="utf-8", errors="surrogateescape")
    return changed


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) != 1:
        print("pemakaian: patch_mrc_c13.py <app/src/main/cpp>", file=sys.stderr)
        return 2
    root = Path(args[0])
    gen = root / "gen"
    if not gen.is_dir():
        print(f"error: {gen} tidak ditemukan", file=sys.stderr)
        return 1

    total = 0
    files = 0
    for cpp in sorted(gen.glob("recompiled_part*.cpp")):
        if cpp.name == "recompiled_part999.cpp":
            continue  # file FIX hasil generate, bukan source translator
        n = rewrite_file(cpp)
        if n:
            files += 1
            total += n

    print(f"OK: {total} mrc c13 TLS-get di-rewrite di {files} file "
          f"(rX = 0x{TLS_PTR:08x}u).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
