#!/usr/bin/env python3
"""Putuskan hash-probe loop yang spins karena mask = 0 (r7 = 0 / state fake).

Loop di 0x817b0cd4..0x817b0cca melakukan linear probe pada tabel hash.  Karena
`ldr.w lr, [r7, 8]` menghasilkan mask 0 (r7 = 0 dari stateguest palsu), index
selalu 0/1 dan tidak pernah menemukan slot kosong (-1) maupun kunci — guest
berputar selamanya.

Patch ini menyisipkan iteration cap pada blok probe: setelah N iterasi,
paksa `r0 = 0xFFFFFFFF` (sentinel slot kosong) dan lompat ke epilogue
0x817b0cea supaya guest bisa lanjut.

Pemakaian:
    python3 tools/patch_spin_break.py <app/src/main/cpp>
"""
from __future__ import annotations

import sys
from pathlib import Path

LOOP_ADDR = "0x817b0cd4"
EXIT_ADDR = "0x817b0cea"
CAP = 4096

OLD = (
    "static void blk_817b0cd4(Cpu& c) {\n"
    "    (void)c;\n"
)
NEW = (
    "static void blk_817b0cd4(Cpu& c) {\n"
    "    (void)c;\n"
    f"    // [SPIN_BREAK] hash-probe mask=0 -> paksa empty-slot setelah {CAP} iterasi\n"
    f"    {{ static uint32_t _spin_it = 0u; if (++_spin_it > {CAP}u) {{\n"
    "        c.r[0] = 0xFFFFFFFFu;\n"
    f"        c.next_pc = {EXIT_ADDR}u; return;\n"
    "    }}\n"
)


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) != 1:
        print("pemakaian: patch_spin_break.py <app/src/main/cpp>", file=sys.stderr)
        return 2
    gen = Path(args[0]) / "gen"
    if not gen.is_dir():
        print(f"error: {gen} tidak ditemukan", file=sys.stderr)
        return 1

    patched = 0
    for cpp in sorted(gen.glob("recompiled_part*.cpp")):
        if cpp.name == "recompiled_part999.cpp":
            continue
        try:
            src = cpp.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            continue
        if OLD not in src or "[SPIN_BREAK]" in src:
            continue
        cpp.write_text(src.replace(OLD, NEW, 1), encoding="utf-8",
                       errors="surrogateescape")
        patched += 1
    print(f"OK: SPIN_BREAK diterapkan di {patched} file (loop {LOOP_ADDR} -> {EXIT_ADDR}, cap={CAP}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
