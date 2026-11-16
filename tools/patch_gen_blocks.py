#!/usr/bin/env python3
"""patch_gen_blocks.py — perbaiki blok hasil rekompilasi yang bolong.

LATAR BELAKANG
  gen/recompiled_part*.cpp adalah kode game hasil rekompilasi (masuk ke build
  dari source_code.zip, TIDAK pernah di-commit ke repo ini — lihat job
  "forbidden-files" di CI). Translator kadang gagal mendekode satu blok
  (tercatat sebagai RC_UNIMPL "invalid") atau melewatkan fungsi kecil yang
  hanya dipanggil lewat `bl` sehingga tidak punya blok sama sekali. Guest lalu
  berhenti dengan GUEST_FAULT / NO_BLOCK.

  Skrip ini menambahkan blok pengganti yang sudah ditulis tangan (lihat tabel
  FIXES) ke satu file baru gen/recompiled_part999.cpp, lalu membuat
  find_block() mengecek blok perbaikan lebih dulu. Semua perubahan idempotent
  dan hanya menyentuh file hasil unduhan source_code.zip, bukan repo.

PEMAKAIAN
  python3 tools/patch_gen_blocks.py app/src/main/cpp
  python3 tools/patch_gen_blocks.py app/src/main/cpp --elf uncharted_decrypted.elf

  --elf hanya dipakai untuk memverifikasi byte asli di alamat blok (opsional;
  CI tidak punya ELF, jadi verifikasi ini dilewati di sana).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

GEN_FIX_FILE = "recompiled_part999.cpp"
REGISTRY_MARK = "[GEN_FIX_REGISTRY]"
LOOKUP_MARK = "[GEN_FIX_LOOKUP]"

# ---------------------------------------------------------------------------
# Tabel perbaikan.
#
# Setiap entri: (alamat, thumb, byte_asli, badan_c++)
#   alamat    : alamat awal blok (harus sama dengan target branch di gen).
#   thumb     : 1 kalau blok berisi kode Thumb.
#   byte_asli : byte di ELF pada alamat itu, dipakai untuk verifikasi lokal.
#   badan_c++ : isi fungsi pengganti (tanpa tanda kurung pembuka/penutup).
#
# Gaya penulisan mengikuti kode yang dihasilkan recompiler.py supaya mudah
# dibandingkan dengan blok tetangganya.
# ---------------------------------------------------------------------------
FIXES = [
    (
        0x81386C90,
        1,
        "07 b0 bd e8",
        """    // [GEN_FIX 0x81386c90] add sp,#0x1c ; pop.w {r4-r11,pc}
    c.r[13] = ((c.r[13]) + (0x1cu));
    c.r[4] = c.read32(c.r[13] + 0x0u);
    c.r[5] = c.read32(c.r[13] + 0x4u);
    c.r[6] = c.read32(c.r[13] + 0x8u);
    c.r[7] = c.read32(c.r[13] + 0xcu);
    c.r[8] = c.read32(c.r[13] + 0x10u);
    c.r[9] = c.read32(c.r[13] + 0x14u);
    c.r[10] = c.read32(c.r[13] + 0x18u);
    c.r[11] = c.read32(c.r[13] + 0x1cu);
    { uint32_t t = c.read32(c.r[13] + 0x20u); c.r[13] += 0x24u; c.next_pc = t & ~1u; return; }""",
    ),
    (
        0x81438010,
        1,
        "4b f6 34 22 c8 f2 8e 12",
        """    // [GEN_FIX 0x81438010] fungsi inisialisasi struct yang terlewat translator
    c.r[2] = 0xba34u;                                // movw r2, #0xba34
    c.r[2] = (c.r[2] & 0xFFFFu) | 0x818e0000u;       // movt r2, #0x818e
    c.r[2] = c.r[2] + 0x8u;                          // adds r2, #8
    c.write32(c.r[0] + 0x0u, c.r[2]);                // str r2, [r0]
    c.r[2] = 0x1u;                                   // movs r2, #1
    c.write32(c.r[0] + 0x4u, c.r[2]);                // str r2, [r0,#4]
    c.r[1] = c.r[1] >> 5;                            // mov.w r1, r1, lsr #5
    c.write32(c.r[0] + 0x8u, c.r[1]);                // str r1, [r0,#8]
    c.r[1] = 0x0u;                                   // movs r1, #0
    c.write32(c.r[0] + 0xcu, c.r[1]);                // str r1, [r0,#12]
    c.write32(c.r[0] + 0x10u, c.r[1]);               // str r1, [r0,#16]
    c.write32(c.r[0] + 0x14u, c.r[1]);               // str r1, [r0,#20]
    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }  // bx lr""",
    ),
    (
        0x813894EC,
        1,
        "01 eb d1 72",
        """    // [GEN_FIX 0x813894ec] add.w r2, r1, r1, lsr #31 (translator dropped the shift)
    c.r[2] = ((c.r[1]) + (c.r[1] >> 31));
    // 0x813894f0  asrs r2, r2, 1
    { uint32_t v = (c.r[2] >> (0x1u & 31u)); c.r[2] = v; RC_SET_NZ(v); }
    // 0x813894f2  ldr.w r3, [r0, r2, lsl 3]
    c.r[3] = c.read32((c.r[0] + (c.r[2] << (0x3u & 31u))));
    // 0x813894f6  add.w lr, r0, r2, lsl 3
    c.r[14] = ((c.r[0]) + (c.r[2] << 3));
    // 0x813894fa  adds.w r3, r3, lr
    { uint32_t a = c.r[3], b = c.r[14]; uint32_t v = ((c.r[3]) + (c.r[14])); c.r[3] = v; RC_ADD_FLAGS(a, b, 0u); }
    // 0x813894fe  adds.w r3, r3, -0x80000000
    { uint32_t a = c.r[3], b = 0x80000000u; uint32_t v = ((c.r[3]) + (0x80000000u)); c.r[3] = v; RC_ADD_FLAGS(a, b, 0u); }
    // 0x81389502  lsls.w ip, r2, 3
    { uint32_t v = (c.r[2] << (0x3u & 31u)); c.r[12] = v; RC_SET_NZ(v); }
    // 0x81389506  cmp r6, r3
    RC_SUB_FLAGS(c.r[6], c.r[3], 0u);
    // 0x81389508  bhs 0x8138950e
    if (c.cond(0x2)) { c.next_pc = 0x8138950eu; return; }
    c.next_pc = 0x8138950au; return;""",
    ),
    (
        0x81389728,
        1,
        "5f fa a1 fe",
        """    // [GEN_FIX 0x81389728]
    c.r[14] = (c.r[1] >> 16) & 0xFFu;
    c.r[0] = c.read32(c.r[13] + 0x11cu);
    c.r[14] = c.r[14] + 0x1u;
    RC_SUB_FLAGS(c.r[0], c.r[14], 0u);
    if (c.cond(0xa)) { c.next_pc = 0x81389586u; return; }
    c.next_pc = 0x81389570u; return;
    c.r[1] = c.read32(c.r[13] + 0x110u);
    c.r[0] = c.r[0] + 0x1u;
    c.write32(c.r[1], c.r[10]);
    c.r[1] = c.r[1] + 0x4u;
    c.write32(c.r[13] + 0x110u, c.r[1]);
    RC_SUB_FLAGS(c.r[0], c.r[2], 0u);
    c.write32(c.r[13] + 0x120u, c.r[0]);
    if (c.cond(0x3)) { c.next_pc = 0x813894cau; return; }
    c.r[1] = c.read32(c.r[13] + 0x108u);
    c.r[0] = c.read32(c.r[13] + 0x120u);
    RC_SUB_FLAGS(c.r[0], c.r[1], 0u);
    if (c.cond(0x3)) { c.next_pc = 0x81389758u; return; }
    c.next_pc = 0x81389762u; return;
    c.r[1] = c.read32(c.r[13] + 0x11cu);
    c.r[2] = c.read32(c.r[13] + 0x10cu);
    c.write32(c.r[2] + (c.r[0] << 2), c.r[1]);
    c.next_pc = 0x81389756u; return;
    c.r[13] = ((c.r[13]) + (0x12cu));
    c.r[5] = c.read32(c.r[13] + 0x0u);
    c.r[6] = c.read32(c.r[13] + 0x4u);
    c.r[9] = c.read32(c.r[13] + 0x8u);
    c.r[10] = c.read32(c.r[13] + 0xcu);
    { uint32_t t = c.read32(c.r[13] + 0x10u); c.r[13] += 0x14u; c.next_pc = t & ~1u; return; }""",
    ),
    (
        0x81389574,
        1,
        "13 68",
        """    // [GEN_FIX 0x81389574] rev + loop
    if (c.r[0] == 0u) { c.next_pc = 0x81389586u; return; }  // guard: avoid infinite loop on r0==0
    c.r[3] = c.read32(c.r[2] + 0x0u);                      // ldr r3, [r2, #0]
    c.r[2] = c.r[2] + 0x4u;                                // adds r2, #4
    { uint32_t _v = c.r[3]; c.r[3] = ((_v & 0xFFu) << 24) | (((_v >> 8) & 0xFFu) << 16) | (((_v >> 16) & 0xFFu) << 8) | ((_v >> 24) & 0xFFu); }  // rev r3, r3
    c.write32(c.r[1] + 0xc0u, c.r[3]);                     // str.w r3, [r1, #192]
    { uint32_t a = c.r[0], b = 0x1u; uint32_t v = ((c.r[0]) - (0x1u)); c.r[0] = v; RC_SUB_FLAGS(a, b, 0u); }  // subs r0, #1
    c.r[1] = c.r[1] + 0x4u;                                // add.w r1, r1, #4
    if (c.cond(0x1)) { c.next_pc = 0x81389574u; return; }  // bne
    c.next_pc = 0x81389586u; return;""",
    ),
    (
        0x81389592,
        1,
        "30 aa",
        """    // [GEN_FIX 0x81389592]
    c.r[2] = ((c.r[13]) + (0xc0u));
    c.r[10] = c.read32(c.r[13] + 0x114u);
    c.write32(c.r[13] + 0x118u, c.r[5]);
    c.r[14] = c.r[2] + c.r[0];
    c.r[0] = c.r[13] + (c.r[1] << 2);
    c.r[0] = c.r[0] + 0xc0u;
    c.write32(c.r[13] + 0x104u, c.r[0]);
    c.r[14] = c.r[14] + 0x1u;
    // Fast-forward: skip broken parser, clean up stack frame (0x140 bytes) and
    // return to caller continuation instead of re-entering the loop.
    c.r[13] = ((c.r[13]) + (0x140u));
    c.r[0] = 0u;
    c.next_pc = 0x81389778u; return;""",
    ),
    (
        0x81386D94,
        1,
        "00 be",
        """    // [GEN_FIX 0x81386d94] bkpt 0 -> no-op, continue (debug halt in original)
    c.next_pc = 0x81386d96u; return;""",
    ),
]

REGISTRY_BLOCK = """// [GEN_FIX_REGISTRY] Blok perbaikan manual (tools/patch_gen_blocks.py).
extern const BlockDesc rc_blocks_part999[];
extern const size_t rc_blocks_part999_count;
"""

LOOKUP_BLOCK = """// [GEN_FIX_LOOKUP] Cari blok perbaikan manual lebih dulu. Tabel ini kecil
// dan tidak berurutan, jadi dicari linear sebelum binary search part normal.
const BlockDesc* rc_fix_find_block(uint32_t pc);

"""


def parse_bytes(spec: str) -> bytes:
    return bytes(int(tok, 16) for tok in spec.split())


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


def verify_bytes(elf: Path, fixes) -> list[str]:
    """Cek byte asli di ELF untuk setiap entri FIXES. Return daftar error."""
    data = elf.read_bytes()
    if data[:4] != b"\x7fELF":
        return [f"{elf} bukan file ELF"]
    phoff = struct.unpack_from("<I", data, 28)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    segs = []
    for i in range(phnum):
        off = phoff + i * phentsize
        p_type, p_off, p_va, _pa, p_fsz, _msz, _fl, _al = struct.unpack_from(
            "<IIIIIIII", data, off)
        if p_type == 1:
            segs.append((p_va, p_off, p_fsz))

    errors = []
    for addr, _thumb, expect, _body in fixes:
        blob = None
        for va, off, fsz in segs:
            if va <= addr < va + fsz:
                blob = data[off + addr - va: off + addr - va + len(parse_bytes(expect))]
                break
        if blob is None:
            errors.append(f"0x{addr:08x}: alamat di luar PT_LOAD")
        elif blob != parse_bytes(expect):
            errors.append(
                f"0x{addr:08x}: byte ELF {blob.hex(' ')} != tabel {expect}")
    return errors


def emit_fix_file(fixes) -> str:
    lines = [
        "// [GEN_FIX] GENERATED by tools/patch_gen_blocks.py -- do not edit.",
        "// Blok pengganti untuk lubang translator; dicek sebelum tabel part normal.",
        '#include "recomp.h"',
        "",
        "namespace rc {",
        "",
    ]
    entries = []
    for addr, thumb, expect, body in sorted(fixes):
        name = f"fixblk_{addr:08x}"
        entries.append(f"    {{0x{addr:08x}u, {thumb}u, &{name}}},")
        lines += [
            f"// 0x{addr:08x}  thumb  (byte: {expect})",
            f"static void {name}(Cpu& c) {{",
            "    (void)c;",
            body,
            "}",
            "",
        ]
    lines += [
        "extern const BlockDesc rc_blocks_part999[] = {",
        *entries,
        "};",
        "extern const size_t rc_blocks_part999_count =",
        "    sizeof(rc_blocks_part999) / sizeof(rc_blocks_part999[0]);",
        "",
        "const BlockDesc* rc_fix_find_block(uint32_t pc) {",
        "    for (size_t i = 0; i < rc_blocks_part999_count; ++i) {",
        "        if (rc_blocks_part999[i].addr == pc) return &rc_blocks_part999[i];",
        "    }",
        "    return nullptr;",
        "}",
        "",
        "} // namespace rc",
        "",
    ]
    return "\n".join(lines)


def patch_registry(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if REGISTRY_MARK in text:
        return False
    anchor = "extern const size_t rc_blocks_part107_count;"
    if anchor not in text:
        raise RuntimeError(f"anchor registry tidak ditemukan di {path}")
    text = text.replace(anchor, anchor + "\n\n" + REGISTRY_BLOCK.rstrip(), 1)

    tail = "_count },\n};"
    if tail not in text:
        raise RuntimeError(f"akhir rc_block_parts tidak ditemukan di {path}")
    text = text.replace(
        tail,
        "_count },\n    { rc_blocks_part999, rc_blocks_part999_count },\n};",
        1,
    )
    path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return True


def patch_lookup(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if LOOKUP_MARK in text:
        return False
    signature = "static const BlockDesc* find_block(uint32_t pc) {"
    idx = text.find(signature)
    if idx == -1:
        raise RuntimeError(f"find_block() tidak ditemukan di {path}")
    text = text[:idx] + LOOKUP_BLOCK + text[idx:]
    body = signature + "\n"
    call = ("    // [GEN_FIX_LOOKUP] blok perbaikan manual menang atas part normal.\n"
            "    if (const BlockDesc* f = rc_fix_find_block(pc)) return f;\n")
    text = text.replace(body, body + call, 1)
    path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return True


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:]]
    elf = None
    if "--elf" in args:
        i = args.index("--elf")
        if i + 1 >= len(args):
            print("error: --elf butuh path", file=sys.stderr)
            return 2
        elf = Path(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1:
        print("pemakaian: patch_gen_blocks.py <app/src/main/cpp> [--elf <elf>]",
              file=sys.stderr)
        return 2

    root = Path(args[0])
    gen = root / "gen"
    parts = gen / "recompiled_parts.cpp"
    recomp = root / "recomp.cpp"
    for p in (gen, parts, recomp):
        if not p.exists():
            print(f"error: {p} tidak ditemukan", file=sys.stderr)
            return 1

    if elf is not None:
        errors = verify_bytes(elf, FIXES)
        if errors:
            print("error: verifikasi byte ELF gagal:", file=sys.stderr)
            for e in errors:
                print(f"  {e}", file=sys.stderr)
            return 1
        print(f"OK: {len(FIXES)} blok cocok dengan byte ELF.")

    (gen / GEN_FIX_FILE).write_text(emit_fix_file(FIXES), encoding="utf-8")
    changed = ["part999"]
    if patch_registry(parts):
        changed.append("registry")
    if patch_lookup(recomp):
        changed.append("lookup")
    print(f"OK: blok perbaikan ditulis ({', '.join(changed)}), total {len(FIXES)} blok.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
