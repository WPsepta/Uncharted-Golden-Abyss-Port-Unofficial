#!/usr/bin/env python3
"""Rewrite `ldrd` / `strd` (load/store register pair) yang belum diterjemahkan.

Translator tidak punya aturan untuk LDRD/STRD Thumb-2, sehingga barisnya jadi
`RC_UNIMPL(...)` dan guest fault.  Instruksi ini murni memory load/store pasangan
32-bit — tidak ada HLE di sini — jadi cukup ditulis ulang jadi dua `read32` /
`write32` pada alamat base+offset (plus writeback bila ada).

Dijalankan paling akhir (setelah regen & patch_mrc_c13) supaya semua part
termasuk part998 ter-coverage.

Pemakaian:
    python3 tools/patch_ldrd_strd.py <app/src/main/cpp>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Register alias ARM -> index.
REGS = {
    **{f"r{i}": i for i in range(16)},
    "sp": 13, "lr": 14, "pc": 15, "ip": 12,
    "fp": 11, "sl": 10, "sb": 9,
}

PAT = re.compile(
    r'^(?P<indent>[ \t]*)RC_UNIMPL\((?P<addr>0x[0-9a-f]+u), '
    r'"(?P<op>ldrd|ldrdal|strd|strdal)\s+'
    r'(?P<r1>[a-z0-9]+),\s*(?P<r2>[a-z0-9]+),\s*'
    r'\[(?P<base>[a-z0-9]+)(?P<inside>[^\]]*)\]'
    r'(?P<wb>!?)(?P<post>[^"]*)"\);(?P<tail>.*)$'
)


def parse_off(s: str) -> int:
    s = s.strip()
    if not s or s == ",":
        return 0
    s = s.lstrip(",").strip()
    if not s:
        return 0
    if s == "-0":
        return 0
    try:
        return int(s, 0)
    except ValueError:
        return 0


def rewrite_line(line: str) -> str | None:
    nl = ""
    body = line
    if body.endswith("\r\n"):
        body, nl = body[:-2], "\r\n"
    elif body.endswith("\n"):
        body, nl = body[:-1], "\n"
    m = PAT.match(body)
    if not m:
        return None
    op = m.group("op")
    r1 = REGS.get(m.group("r1"))
    r2 = REGS.get(m.group("r2"))
    base = REGS.get(m.group("base"))
    if r1 is None or r2 is None or base is None:
        return None
    addr = m.group("addr")
    indent = m.group("indent")
    inside_off = parse_off(m.group("inside"))
    wb = m.group("wb") == "!"
    post_off = parse_off(m.group("post"))
    is_load = op.startswith("ldr")

    lines = [f"{indent}{{ // [LDRD_STRD] {addr} {op}"]
    # alamat efektif
    if inside_off >= 0:
        lines.append(f"{indent}    uint32_t _a = c.r[{base}] + 0x{inside_off:x}u;")
    else:
        lines.append(f"{indent}    uint32_t _a = c.r[{base}] - 0x{-inside_off:x}u;")
    # writeback
    if wb:
        if inside_off >= 0:
            lines.append(f"{indent}    c.r[{base}] += 0x{inside_off:x}u;")
        else:
            lines.append(f"{indent}    c.r[{base}] -= 0x{-inside_off:x}u;")
    elif m.group("post").strip().lstrip(",").strip():
        if post_off >= 0:
            lines.append(f"{indent}    c.r[{base}] += 0x{post_off:x}u;")
        else:
            lines.append(f"{indent}    c.r[{base}] -= 0x{-post_off:x}u;")
    # akses memori pasangan
    if is_load:
        lines.append(f"{indent}    c.r[{r1}] = c.read32(_a);")
        lines.append(f"{indent}    c.r[{r2}] = c.read32(_a + 4u);")
    else:
        # PC sebagai sumber: nilai arsitektural = current_pc + 4 (Thumb).
        v1 = "c.r[%d]" % r1 if r1 != 15 else "(c.current_pc + 4u)"
        v2 = "c.r[%d]" % r2 if r2 != 15 else "(c.current_pc + 4u)"
        lines.append(f"{indent}    c.write32(_a, {v1});")
        lines.append(f"{indent}    c.write32(_a + 4u, {v2});")
    lines.append(f"{indent}}}")
    return "\n".join(lines) + nl


def rewrite_file(path: Path) -> int:
    try:
        src = path.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return 0
    out: list[str] = []
    n = 0
    for line in src.splitlines(keepends=True):
        new = rewrite_line(line)
        if new is None:
            out.append(line)
        else:
            out.append(new)
            n += 1
    if n:
        path.write_text("".join(out), encoding="utf-8", errors="surrogateescape")
    return n


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) != 1:
        print("pemakaian: patch_ldrd_strd.py <app/src/main/cpp>", file=sys.stderr)
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
    print(f"OK: {total} ldrd/strd di-rewrite di {files} file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
