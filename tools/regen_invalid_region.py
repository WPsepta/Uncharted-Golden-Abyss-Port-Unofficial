#!/usr/bin/env python3
"""regen_invalid_region.py — bangkitkan ulang blok Thumb-2 yang gagal diterjemahkan.

LATAR BELAKANG
  recompiler.py memakai Rizin `pd` yang mendeteksi mode (ARM/Thumb) dari alamat
  fungsi. Di region 0x81386d9a..0x81387592 fungsi game tidak punya symbol,
  sehingga Rizin mendekode sebagai ARM 32-bit -> semua instruksi jadi "invalid"
  dan translator menghasilkan RC_UNIMPL. Padahal byte aslinya adalah Thumb-2
  (movw/movt/str/ldr/bl/...) yang SEBENARNYA bisa diterjemahkan.

  Tool ini mendekode ulang region itu PAKSA Thumb-16 lewat `rz-asm -d -b 16`,
  lalu memakai Emitter.lower() dari recompiler.py yang sama untuk menghasilkan
  blok C++ yang benar. Hasilnya ditulis ke gen/recompiled_part998.cpp dan
  didaftarkan ke recompiled_parts.cpp (part998) + find_block lookup.

  Output idempotent dan hanya menyentuh file hasil source_code.zip.

PEMAKAIAN
  python3 tools/regen_invalid_region.py <app/src/main/cpp> [--elf <elf>]
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

# Rentang yang mau dibangkitkan ulang (blok invalid berturut setelah create-thread).
# Daftar region blok invalid yang dibangkitkan ulang (start, end).
REGIONS = [
    (0x81386D9A, 0x81387592),    # 16 blok invalid setelah create-thread
    (0x8138974E, 0x81389768),    # jalur early-exit parser (r6=0)
]
REGION_START, REGION_END = REGIONS[0]
PART_FILE = "recompiled_part998.cpp"
PART_SYM = "rc_blocks_part998"
MARK = "[REGEN_INVALID]"

# file offset = va - 0x81000000 + 0x1000 (PT_LOAD pertama, lihat verify_bytes)
VA_BASE = 0x81000000
FILE_BIAS = 0x1000


def v2o(va: int) -> int:
    return FILE_BIAS + (va - VA_BASE)


def rizin_pd_thumb(elf: Path, start: int, count: int) -> list[dict]:
    """Disassemble `count` Thumb-2 instructions at `start` via Rizin aoj/pdj.

    Marker `ahb 16` memaksa Rizin meng decoding region sebagai Thumb, sehingga
    movw/movt (4-byte) dan target branch absolut didapat dengan benar.
    """
    cmd = (
        "e asm.arch=arm; e asm.bits=16; aa; "
        f"ahb 16 @ {start:#x}; pdj {count} @ {start:#x}"
    )
    p = subprocess.run(
        ["rizin", "-q", "-e", "scr.color=0", "-c", cmd, str(elf)],
        capture_output=True, text=True,
    )
    try:
        return json.loads(p.stdout)
    except Exception:
        return []


def decode_region(elf: Path) -> list[tuple[int, int, str, int | None]]:
    """Return (addr, size, text, jump) for instructions in [REGION_START, REGION_END).

    Rizin kehilangan mode Thumb setelah `blx` ke fungsi lain, jadi kita decode
    per-chunk kecil: setiap chunk diberi `ahb 16` di alamat awalnya supaya mode
    Thumb selalu dipaksa. Chunk berikutnya mulai dari instruksi terakhir chunk
    sebelumnya (bukan byte), sehingga tidak ada yang terlewat/duplikat.
    """
    out: list[tuple[int, int, str, int | None]] = []
    seen: set[int] = set()
    CHUNK = 12
    for rstart, rend in REGIONS:
        cur = rstart
        guard = 0
        while cur < rend and guard < 400:
            guard += 1
            raw = rizin_pd_thumb(elf, cur, CHUNK)
            if not raw:
                break
            next_cur = None
            for o in raw:
                try:
                    addr = int(o.get("offset", 0))
                    size = int(o.get("size", 2) or 2)
                    text = (o.get("opcode") or o.get("disasm") or "").strip()
                    jump = o.get("jump")
                    jump = int(jump) if jump is not None else None
                except (ValueError, TypeError):
                    continue
                if addr in seen or addr < rstart or addr >= rend:
                    continue
                seen.add(addr)
                out.append((addr, size, text, jump))
                if next_cur is None or addr >= next_cur:
                    next_cur = addr + size
            if next_cur is None or next_cur <= cur:
                break
            cur = next_cur
    out.sort(key=lambda x: x[0])
    return out


def is_terminator(text: str) -> bool:
    t = text.strip().lower()
    if t.startswith("b ") or t.startswith("b.") or re.match(r"^b(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)\b", t):
        return True
    if t.startswith("bx ") or t.startswith("blx "):
        return True
    if t.startswith("pop ") and "pc" in t:
        return True
    return False


def branch_target(text: str, addr: int, size: int) -> int | None:
    """Best-effort绝对 branch target for Thumb B/B<cond> instructions."""
    m = re.match(r"^b(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)?\s+(0x[0-9a-f]+|\d+)\b", text.strip(), re.I)
    if not m:
        return None
    try:
        return int(m.group(2), 0)
    except ValueError:
        return None


def build_blocks(ops: list[tuple[int, int, str, int | None]]):
    """Split linear ops into basic blocks at branch targets and after terminators."""
    targets = set()
    for addr, size, text, jump in ops:
        t = jump if jump is not None else branch_target(text, addr, size)
        if t is not None and any(rs <= t < re for rs, re in REGIONS):
            targets.add(t)
    blocks = []
    cur = []
    # setiap alamat awal region adalah batas blok (region tidak boleh digabung)
    region_starts = {rs for rs, _ in REGIONS}
    for op in ops:
        addr, size, text, jump = op
        if cur and (addr in targets or addr in region_starts):
            blocks.append(cur)
            cur = []
        cur.append(op)
        if is_terminator(text):
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def load_recompiler():
    sys.argv = ["recompiler.py"]
    # recompiler.py hidup di repo game (/root/uncharted_re/tools); coba beberapa lokasi.
    candidates = [
        Path("/root/uncharted_re/tools/recompiler.py"),
        Path(__file__).resolve().parent.parent / "uncharted_re" / "tools" / "recompiler.py",
        Path(__file__).resolve().parent / "recompiler.py",
    ]
    rp = next((p for p in candidates if p.exists()), candidates[0])
    spec = importlib.util.spec_from_file_location("rc_recompiler", rp)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rc_recompiler"] = mod
    spec.loader.exec_module(mod)
    return mod


def emit(elf: Path, gen: Path) -> bool:
    out_file = gen / PART_FILE
    if out_file.exists() and MARK in out_file.read_text(errors="surrogateescape"):
        return False  # idempotent

    rc = load_recompiler()
    ops = decode_region(elf)
    blocks = build_blocks(ops)

    em = rc.Emitter.__new__(rc.Emitter)
    # minimal state needed by emit_block/lower
    em.fh = None
    em.path = str(elf)
    em.part_index = 998
    em.blocks = []
    em.part_lines = 0
    em.split_funcs = 0
    em.split_lines = 0
    import io
    buf = io.StringIO()
    em.fh = buf

    lines = [
        f"// {MARK} GENERATED by tools/regen_invalid_region.py -- do not edit.",
        "// Thumb-2 region 0x81386d9a..0x81387592, di-decode ulang dengan rz-asm -b 16.",
        '#include "recomp.h"',
        "namespace {",
        "using ::rc::Cpu;",
        "",
    ]
    buf.write("\n".join(lines) + "\n")

    blk_addrs = []
    for b in blocks:
        if not b:
            continue
        first_addr = b[0][0]
        op_objs = []
        for addr, size, text, jump in b:
            op_objs.append(rc.Op(
                addr=addr, size=size, text=text, disasm=text,
                type="", jump=jump, fail=None, thumb=True,
            ))
        blk = rc.Block(addr=first_addr, thumb=True, ops=op_objs)
        next_addr = b[-1][0] + b[-1][1]
        em.emit_block(blk, next_addr)
        blk_addrs.append(first_addr)

    # Post-process: bkpt/mrc yang masih RC_UNIMPL diubah jadi no-op lanjut,
    # supaya tidak memicu RC_UNIMPL fault di tengah fungsi (sama seperti
    # perlakuan fix 0x81386d94). Kita tulis ulang buffer dengan substitusi.
    txt = buf.getvalue()
    def _noop_unimpl(m):
        addr = int(m.group(1), 16)
        nxt = addr + 2
        return (f"    // bkpt/system @0x{addr:08x} -> no-op, lanjut ke 0x{nxt:x}\n"
                f"    c.next_pc = 0x{nxt:x}u; return;")
    txt = re.sub(
        r'    RC_UNIMPL\(0x([0-9a-f]+)u, "(?:bkpt 0|mrc [^"]*)"\);  // [^\n]*',
        _noop_unimpl, txt,
    )
    buf.seek(0)
    buf.truncate()
    buf.write(txt)

    buf.write("} // namespace\n\nnamespace rc {\n")
    buf.write(f"extern const BlockDesc {PART_SYM}[] = {{\n")
    for a in sorted(blk_addrs):
        buf.write(f"    {{0x{a:08x}u, 1u, &blk_{a:08x}}},\n")
    buf.write("};\n")
    buf.write(f"extern const size_t {PART_SYM}_count = sizeof({PART_SYM}) / sizeof({PART_SYM}[0]);\n")
    buf.write("} // namespace rc\n")

    out_file.write_text(buf.getvalue(), encoding="utf-8", errors="surrogateescape")
    return True


def patch_registry(parts_file: Path) -> bool:
    """Deklarasikan simbol part998 di recompiled_parts.cpp.

    Sengaja TIDAK menambah part998 ke rc_block_parts[]: array itu di-binary-search
    berdasarkan alamat blok pertama tiap part, dan alamat part998 (0x81386d9a)
    berada di tengah rentang part042 — kalau ditaruh di akhir array, binary search
    tidak akan pernah menemukannya. Sebagai gantinya, patch_gen_blocks.py yang
    menyuntik lookup `rc_fix_find_block()` akan mencari part998 secara linear
    LEBIH DAHULU (lihat LOOKUP_BLOCK di patch_gen_blocks.py).
    """
    text = parts_file.read_text(encoding="utf-8", errors="surrogateescape")
    if MARK in text:
        return False
    anchor = "extern const size_t rc_blocks_part999_count;"
    if anchor not in text:
        raise RuntimeError(f"anchor {anchor} tidak ditemukan")
    decl = (
        f"// {MARK} part998 blok hasil regenerate Thumb-2.\n"
        f"// TIDAK masuk rc_block_parts[] (address-ordered binary search);\n"
        f"// dicari linear lebih dulu via rc_fix_find_block().\n"
        f"extern const BlockDesc {PART_SYM}[];\n"
        f"extern const size_t {PART_SYM}_count;\n"
    )
    text = text.replace(anchor, decl + anchor, 1)
    parts_file.write_text(text, encoding="utf-8", errors="surrogateescape")
    return True


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:]]
    elf = None
    if "--elf" in args:
        i = args.index("--elf")
        elf = Path(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1:
        print("pemakaian: regen_invalid_region.py <app/src/main/cpp> [--elf <elf>]", file=sys.stderr)
        return 2
    root = Path(args[0])
    gen = root / "gen"
    parts = gen / "recompiled_parts.cpp"
    if not gen.exists() or not parts.exists():
        print(f"error: {gen} / {parts} tidak ditemukan", file=sys.stderr)
        return 1
    changed = []
    outf = gen / PART_FILE

    # Mode CI (tanpa ELF): salin data hasil generate yang sudah di-commit.
    # Mode lokal dengan --elf: decode ulang pakai Rizin (butuh ELF + recompiler).
    inc = Path(__file__).resolve().parent / "recompiled_part998.cpp.inc"
    if elf is not None and elf.exists():
        if emit(elf, gen):
            changed.append("part998(regen)")
    elif inc.exists():
        if not outf.exists() or MARK not in outf.read_text(errors="surrogateescape"):
            outf.write_text(inc.read_text(errors="surrogateescape"),
                            encoding="utf-8", errors="surrogateescape")
            changed.append("part998(inc)")
    else:
        print("error: tidak ada --elf dan tidak ada recompiled_part998.cpp.inc", file=sys.stderr)
        return 1

    if patch_registry(parts):
        changed.append("registry")
    n_blk = 0
    if outf.exists():
        n_blk = outf.read_text(errors="surrogateescape").count("static void blk_")
        assert n_blk > 0, "part998 tidak punya blok"
    print(f"OK: {n_blk} blok Thumb-2 part998 ({', '.join(changed) or 'sudah ada'}), "
          f"regions: {', '.join('0x%x..0x%x' % r for r in REGIONS)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
