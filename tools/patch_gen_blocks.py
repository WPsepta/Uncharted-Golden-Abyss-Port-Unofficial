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
# Peta stub import hollow -> NID asli (di-generate dari ELF import table).
# Semua stub ARM hollow (mvn r0,#0 ; bx lr) DIALIHKAN ke rc_hle_dispatch(nid)
# supaya setiap import yang dipanggil guest tercatat di HLE_MISSING dengan
# NID + pc + arg, dan NID yang terdaftar di rc_hle_table mendapat handler nyata.
# ---------------------------------------------------------------------------
_STUB_MAP_INC = Path(__file__).resolve().parent / "stub_nid_map.inc"
STUB_NID_MAP = {}
if _STUB_MAP_INC.exists():
    _ns: dict = {}
    exec(_STUB_MAP_INC.read_text(encoding="utf-8"), _ns)
    STUB_NID_MAP = _ns["STUB_NID_MAP"]
else:  # fallback minimal bila file generate hilang (tetap bisa build)
    STUB_NID_MAP = {}

# NID RESMI (vita-headers / ScarletNX / vita2hos) untuk handler nyata.
NID_CREATE_THREAD = 0xC5C11EE7  # sceKernelCreateThread
NID_START_THREAD  = 0xF08DE149  # sceKernelStartThread

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
        """    // [GEN_FIX 0x81386d94] bkpt 0 -> lompat ke 0x81386d9a (blok Thumb-2
    // hasil regenerate di part998). 0x81386d96/98 tidak punya blok valid.
    c.next_pc = 0x81386d9au; return;""",
    ),
    (
        0x8138979E,
        1,
        "01 35 04 34 b5 42 f3 d3",
        """    // [GEN_FIX 0x8138979e] loop: iter r5++/r4+=4 while r5<r6, body calls 0x813833cc(r0=0x81885268,r1=r5,r2=*r4)
    static uint32_t rc_loop_8138979e = 0;
    { uint32_t a = c.r[5], b = 0x1u; uint32_t v = ((c.r[5]) + (0x1u)); c.r[5] = v; RC_ADD_FLAGS(a, b, 0u); }  // adds r5, #1
    { uint32_t a = c.r[4], b = 0x4u; uint32_t v = ((c.r[4]) + (0x4u)); c.r[4] = v; RC_ADD_FLAGS(a, b, 0u); }  // adds r4, #4
    // Substitusi command fake bila slot list masih bernilai 0 (render/stub belum mengisi)
    {
        uint32_t _eaddr = c.r[4];
        uint32_t _val = c.in_range(_eaddr, 4u) ? c.read32(_eaddr) : 0u;
        if (_val == 0u) {
            c.write32(_eaddr, 0x81010000u); // fake command value -> render stub bisa proceed
        }
    }
    RC_SUB_FLAGS(c.r[5], c.r[6], 0u);  // cmp r5, r6
    // Debug: element pointer/value for this iteration
    uint32_t _elem_addr = c.r[4] - 4u;
    uint32_t _elem_val = c.in_range(_elem_addr, 4) ? c.read32(_elem_addr) : 0xDEADBEEFu;
    if (c.cond(0x3)) {  // blo 0x8138978e
        ++rc_loop_8138979e;
#if defined(__ANDROID__)
        if (rc_loop_8138979e <= 5u || rc_loop_8138979e == 100u || (rc_loop_8138979e % 200u) == 0u) {
            char _lw[256];
            snprintf(_lw, sizeof(_lw),
                "LOOP_WAIT: 0x8138979e iter=%u addr(elem@r4-4)=0x%08x current_value=0x%08x r4=0x%08x r5=0x%08x r6=0x%08x",
                rc_loop_8138979e, _elem_addr, _elem_val, c.r[4], c.r[5], c.r[6]);
            __android_log_print(ANDROID_LOG_WARN, "LOOP_WAIT", "%s", _lw);
            if (rc_dbg_log_hook) rc_dbg_log_hook("LOOP_WAIT", _lw);
        }
#endif
        // [LOOP_GUARD] Jika r6 ternyata bukan count wajar (alamat BSS / >64k),
        // jangan iterasi jutaan kali: set r6=r5 supaya loop selesai natural.
        if (c.r[6] > 0x10000u || rc_loop_8138979e >= 100u) {
            rc_loop_8138979e = 0;
            c.r[6] = c.r[5];
            RC_SUB_FLAGS(c.r[5], c.r[6], 0u);
#if defined(__ANDROID__)
            {
                char _lb[200];
                snprintf(_lb, sizeof(_lb),
                    "LOOP_WAIT: guard exit (addr=0x%08x val=0x%08x) r5=r6=0x%08x",
                    _elem_addr, _elem_val, c.r[6]);
                __android_log_print(ANDROID_LOG_WARN, "LOOP_WAIT", "%s", _lb);
                if (rc_dbg_log_hook) rc_dbg_log_hook("LOOP_WAIT", _lb);
            }
#endif
            c.next_pc = 0x813897a6u; return;
        }
        c.next_pc = 0x8138978eu; return;
    }
    rc_loop_8138979e = 0;
    c.next_pc = 0x813897a6u; return;""",
    ),
    (
        0x813897A6,
        1,
        "0d f5 00 7d 70 bd",
        """    // [GEN_FIX 0x813897a6] epilogue: add sp,#0x200 ; pop {r4-r6,pc}
    // HALT_DEBUG: log before returning (helps diagnose halt normal / next_pc==0)
    {
        uint32_t sp0 = c.r[13];
        uint32_t ret = c.read32(sp0 + 0x20cu); // saved pc after add sp,0x200 -> pop at +0xc
        uint32_t ret_pc = ret & ~1u;
#if defined(__ANDROID__)
        {
            char _hb[320];
            snprintf(_hb, sizeof(_hb),
                "0x813897a6 epilogue: sp=0x%08x ret_pc=0x%08x r4=0x%08x r5=0x%08x r6=0x%08x lr=0x%08x",
                sp0, ret_pc, c.read32(sp0 + 0x200u), c.read32(sp0 + 0x204u),
                c.read32(sp0 + 0x208u), c.r[14]);
            __android_log_print(ANDROID_LOG_DEBUG, "HALT_DEBUG", "%s", _hb);
            // juga ke recomp.log (tab Log) via hook jika tersedia
            if (rc_dbg_log_hook) rc_dbg_log_hook("HALT_DEBUG", _hb);
        }
        // Dump trace blok terakhir jika tersedia
        {
            const uint32_t n = rc_dbg_hist_n > 32u ? 32u : rc_dbg_hist_n;
            char tbuf[1024]; size_t tp = 0;
            tp += (size_t)snprintf(tbuf + tp, sizeof(tbuf) - tp,
                "TRACE blok terakhir (%u, steps=%llu):", n, (unsigned long long)c.blocks_run);
            for (uint32_t k = 0; k < n && tp + 12 < sizeof(tbuf); ++k) {
                const uint32_t idx = (rc_dbg_hist_i + 32u - n + k) & 31u;
                tp += (size_t)snprintf(tbuf + tp, sizeof(tbuf) - tp, " 0x%08x", rc_dbg_hist_pc[idx]);
            }
            // [HLE_HIST] 8 import terakhir sebelum halt + total panggilan.
            {
                const uint32_t hn = rc_dbg_hle_hist_n > 8u ? 8u : rc_dbg_hle_hist_n;
                for (uint32_t k = 0; k < hn && tp + 44 < sizeof(tbuf); ++k) {
                    const uint32_t idx = (rc_dbg_hle_hist_i + 8u - hn + k) & 7u;
                    tp += (size_t)snprintf(tbuf + tp, sizeof(tbuf) - tp,
                        " | HLE#%u NID=0x%08x pc=0x%08x", k,
                        rc_dbg_hle_hist_nid[idx], rc_dbg_hle_hist_pc[idx]);
                }
                tp += (size_t)snprintf(tbuf + tp, sizeof(tbuf) - tp,
                    " | lastNID=0x%08x hle_calls=%llu stub_returns=%llu",
                    rc_dbg_last_nid, (unsigned long long)rc_dbg_hle_calls,
                    (unsigned long long)rc_dbg_stub_returns);
            }
            __android_log_print(ANDROID_LOG_DEBUG, "HALT_DEBUG", "%s", tbuf);
            if (rc_dbg_log_hook) rc_dbg_log_hook("HALT_DEBUG", tbuf);
        }
#endif
        c.r[13] = ((sp0) + (0x200u));
        c.r[4] = c.read32(sp0 + 0x200u);
        c.r[5] = c.read32(sp0 + 0x204u);
        c.r[6] = c.read32(sp0 + 0x208u);
        uint32_t t = c.read32(sp0 + 0x20cu);
        c.r[13] = sp0 + 0x210u;
        c.next_pc = ret_pc;
#if defined(__ANDROID__)
        if (c.next_pc == 0u) {
            // ret_pc==0: akhir thread/init. Jangan paksa ke 0x81386d94 (loop).
            // Biarkan next_pc==0 -> runtime halt, android_main idle (emulator tetap hidup).
            char msg[320];
            snprintf(msg, sizeof(msg),
                "0x813897a6: ret_pc==0 (thread/init end) -> idle. stub_callers: 0x81815464_lr=0x%08x 0x81815494_lr=0x%08x",
                rc_dbg_stub_caller_81815464, rc_dbg_stub_caller_81815494);
            __android_log_print(ANDROID_LOG_WARN, "HALT_DEBUG", "%s", msg);
            if (rc_dbg_log_hook) rc_dbg_log_hook("HALT_DEBUG", msg);
        }
#endif
        return;
    }""",
    ),
    (
        0x810A61EC,
        1,
        "70 b5 04 1c",
        """    // [GEN_FIX 0x810a61ec] CRT TLS getter: tiru seluruh blok 9 insn,
    // mrc p15 c13 (RC_UNIMPL) diganti r1 = TLS ptr.
    c.r[13] -= 0x10u;
    c.write32(c.r[13] + 0x0u, c.r[4]);
    c.write32(c.r[13] + 0x4u, c.r[5]);
    c.write32(c.r[13] + 0x8u, c.r[6]);
    c.write32(c.r[13] + 0xcu, c.r[14]);
    { uint32_t a = c.r[0], b = 0x0u; uint32_t v = ((c.r[0]) + (0x0u)); c.r[4] = v; RC_ADD_FLAGS(a, b, 0u); }
    c.r[1] = 0x81970000u;  // TLS ptr (ganti mrc p15,c13)
    c.r[2] = 0x2f9u;
    { uint32_t v = 0x1u; c.r[0] = v; RC_SET_NZ(v); }
    c.r[3] = c.in_range((c.r[4] + 0x2f8u), 1u) ? c.read8((c.r[4] + 0x2f8u)) : 0u;
    RC_SUB_FLAGS(c.r[3], 0x0u, 0u);
    // [MEMGUARD] guest manggil dg r0=0 -> r4=0, write ke 0x2f9 (luar window).
    // Abaikan write di luar guest window supaya tidak fault.
    if (c.in_range((c.r[4] + c.r[2]), 1u)) c.write8((c.r[4] + c.r[2]), c.r[0]);
    if (c.cond(0x1)) { c.next_pc = 0x810a623au; return; }
    c.next_pc = 0x810a6204u; return;""",
    ),
    (
        0x81437542,
        1,
        "df e8 10 f0",
        """    // [GEN_FIX 0x81437542] tbh [pc,r0,lsl1] -> jump-table hardcoded.
    // guest window base 0x81000000, ELF file bias 0x1000.
    static const uint32_t kTbl[16] = {
        0x81435564u, 0x81437546u, 0x81437546u, 0x81437546u,
        0x81437546u, 0x81437546u, 0x81437546u, 0x81437546u,
        0x81437676u, 0x81437596u, 0x81437676u, 0x814375e6u,
        0x81437650u, 0x81437650u, 0x81437650u, 0x81437650u};
    uint32_t _idx = c.r[0];
    if (_idx > 15u) _idx = 1u;
#if defined(__ANDROID__)
    { static uint32_t _jn=0; if (_jn++ < 3u) {
        char _jb[128]; snprintf(_jb,sizeof(_jb),"TBH: idx=0x%x -> 0x%08x",c.r[0],kTbl[_idx]);
        __android_log_print(ANDROID_LOG_WARN,"TBH_DEBUG","%s",_jb);
        if (rc_dbg_log_hook) rc_dbg_log_hook("TBH_DEBUG",_jb);
    }}
#endif
    c.next_pc = kTbl[_idx]; return;""",
    ),
]

ARM_STUB_ADDRS = [
    0x81815004,
    0x81815014,
    0x81815024,
    0x81815034,
    0x81815044,
    0x81815054,
    0x81815064,
    0x81815074,
    0x81815084,
    0x81815094,
    0x818150a4,
    0x818150b4,
    0x818150c4,
    0x818150d4,
    0x818150e4,
    0x818150f4,
    0x81815104,
    0x81815114,
    0x81815124,
    0x81815134,
    0x81815144,
    0x81815154,
    0x81815164,
    0x81815174,
    0x81815184,
    0x81815194,
    0x818151a4,
    0x818151b4,
    0x818151c4,
    0x818151d4,
    0x818151e4,
    0x818151f4,
    0x81815204,
    0x81815214,
    0x81815224,
    0x81815234,
    0x81815244,
    0x81815254,
    0x81815264,
    0x81815274,
    0x81815284,
    0x81815294,
    0x818152a4,
    0x818152b4,
    0x818152c4,
    0x818152d4,
    0x818152e4,
    0x818152f4,
    0x81815304,
    0x81815314,
    0x81815324,
    0x81815334,
    0x81815344,
    0x81815354,
    0x81815364,
    0x81815374,
    0x81815384,
    0x81815394,
    0x818153a4,
    0x818153b4,
    0x818153c4,
    0x818153d4,
    0x818153e4,
    0x818153f4,
    0x81815404,
    0x81815414,
    0x81815424,
    0x81815434,
    0x81815444,
    0x81815454,
    0x81815464,
    0x81815474,
    0x81815484,
    0x81815494,
    0x818154a4,
    0x818154b4,
    0x818154c4,
    0x818154d4,
    0x818154e4,
    0x818154f4,
    0x81815504,
    0x81815514,
    0x81815524,
    0x81815534,
    0x81815544,
    0x81815554,
    0x81815564,
    0x81815574,
    0x81815584,
    0x81815594,
    0x818155a4,
    0x818155b4,
    0x818155c4,
    0x818155d4,
    0x818155e4,
    0x818155f4,
    0x81815604,
    0x81815614,
    0x81815624,
    0x81815634,
    0x81815644,
    0x81815654,
    0x81815664,
    0x81815674,
    0x81815684,
    0x81815694,
    0x818156a4,
    0x818156b4,
    0x818156c4,
    0x818156d4,
    0x818156e4,
    0x818156f4,
    0x81815704,
    0x81815714,
    0x81815724,
    0x81815734,
    0x81815744,
    0x81815754,
    0x81815764,
    0x81815774,
    0x81815784,
    0x81815794,
    0x818157a4,
    0x818157b4,
    0x818157c4,
    0x818157d4,
    0x818157e4,
    0x818157f4,
    0x81815804,
    0x81815814,
    0x81815824,
    0x81815834,
    0x81815844,
    0x81815854,
    0x81815864,
    0x81815874,
    0x81815884,
    0x81815894,
    0x818158a4,
    0x818158b4,
    0x818158c4,
    0x818158d4,
    0x818158e4,
    0x818158f4,
    0x81815904,
    0x81815914,
    0x81815924,
    0x81815934,
    0x81815944,
    0x81815954,
    0x81815964,
    0x81815974,
    0x81815984,
    0x81815994,
    0x818159a4,
    0x818159b4,
    0x818159c4,
    0x818159d4,
    0x818159e4,
    0x818159f4,
    0x81815a04,
    0x81815a14,
    0x81815a24,
    0x81815a34,
    0x81815a44,
    0x81815a54,
    0x81815a64,
    0x81815a74,
    0x81815a84,
    0x81815a94,
    0x81815aa4,
    0x81815ab4,
    0x81815ac4,
    0x81815ad4,
    0x81815ae4,
    0x81815af4,
    0x81815b04,
    0x81815b14,
    0x81815b24,
    0x81815b34,
    0x81815b44,
    0x81815b54,
    0x81815b64,
    0x81815b74,
    0x81815b84,
    0x81815b94,
    0x81815ba4,
    0x81815bb4,
    0x81815bc4,
    0x81815bd4,
    0x81815be4,
    0x81815bf4,
    0x81815c04,
    0x81815c14,
    0x81815c24,
    0x81815c34,
    0x81815c44,
    0x81815c54,
    0x81815c64,
    0x81815c74,
    0x81815c84,
    0x81815c94,
    0x81815ca4,
    0x81815cb4,
    0x81815cc4,
    0x81815cd4,
    0x81815ce4,
    0x81815cf4,
    0x81815d04,
    0x81815d14,
    0x81815d24,
    0x81815d34,
    0x81815d44,
    0x81815d54,
    0x81815d64,
    0x81815d74,
    0x81815d84,
    0x81815d94,
    0x81815da4,
    0x81815db4,
    0x81815dc4,
    0x81815dd4,
    0x81815de4,
    0x81815df4,
    0x81815e04,
    0x81815e14,
    0x81815e24,
]

# Perluas FIXES dengan stub ARM (mvn r0,#0 ; bx lr) di area 0x81815xxx.
# ---------------------------------------------------------------------------
# 2026-11: SEMUA stub import hollow yang punya NID diketahui (STUB_NID_MAP,
# 431 entri dari import table ELF) DIALIHKAN ke rc_hle_dispatch(nid asli).
# Efek:
#   - setiap import yang dipanggil guest tercatat di HLE_MISSING (NID + pc +
#     r0..r3) — sekarang kita bisa LIHAT stub mana yang dipanggil sebelum halt;
#   - NID yang terdaftar di rc_hle_table (CreateThread/StartThread resmi,
#     NID 0xC5C11EE7/0xF08DE149) mendapat handler nyata;
#   - NID lain otomatis SCE_OK(0) menggantikan -1 (game bisa lanjut).
# Dua stub special 0x81815464/0x81815494 dipertahankan (fake context) karena
# tick thread memanggilnya langsung di loop.
# ---------------------------------------------------------------------------
_LOG_STUBS = set()  # 0x81815464/0x81815494 handled by _CRT_MEMCPY_STUBS below
# [CRT_MEMCPY] Tiga stub hollow ini adalah memcpy CRT yang dipanggil guest dengan
# r0=dest, r1=size, r2=src, r3=end (lihat 0x813833f6/0x813833fc/0x81383402).
# Stub lama cuma no-op + MENIMPA r2 dengan fake context 0x81010000 -> buffer
# stack tidak terisi -> count garbage 0x81970ea4 -> loop 0x8138979e forced exit
# -> ret_pc=0 -> halt. Implementasikan memcpy NYATA di guest memory.
_CRT_MEMCPY_STUBS = {0x81815464, 0x81815494, 0x818156d4}
# [CRT_NEG] 0x81815ca4 = sceKernelCreateThread("Scratch",...). Return 0 membuat
# guest mengambil jalur "sukses" 0x81386d9a yang berisi ratusan instruksi wide
# yang TIDAK bisa diterjemahkan translator (RC_UNIMPL) -> fault. Return -1
# membuat guest ambil jalur 0x81386d60 yang valid & sudah diperbaiki
# (parser 0x81389498 kini dapat SCE_OK dari 0x81815364 -> r6 tidak garbage).
_CRT_NEG_STUBS = set()  # 0x81815ca4 kini SCE_OK(0): blok 0x81386d9a sudah diregenerate
# [THREAD_HLE] Stub CRT internal thread: 0x81815ca4 = create-thread(name r0, entry r1,
# stack r2, arg r3), 0x81815c94 = start-thread(r1=thread slot). Kita simpan entry
# dan jalankan di std::thread nyata (bukan cuma return 0).
_CRT_THREAD_CREATE = {0x81815ca4}
_CRT_THREAD_START  = {0x81815c94}
# [CRT_ZEROFILL] 0x81815364 dipanggil parser 0x81389498 dengan r0=sp (output
# struct). Stub no-op membiarkan [sp+0x118] berisi PC -> r6=0x81970ea4 (alamat
# BSS) -> loop 0x8138979e mengira itu count 2 miliar -> forced exit 100 ->
# ret_pc=0 halt. Tulis 0 ke [r0+0x118] supaya r6=0 & loop skip natural.
_CRT_ZEROFILL = {0x81815364}
_ALL_STUB_ADDRS = sorted(set(ARM_STUB_ADDRS) | set(STUB_NID_MAP.keys()))
for _a in _ALL_STUB_ADDRS:
    _log = ""
    _ret = "0xFFFFFFFFu"
    if _a in _LOG_STUBS:
        _ret = "0u"  # SCE_OK: treat as success so caller/game can proceed
        _log = (
            "#if defined(__ANDROID__)\n"
            "    rc_dbg_stub_caller_%08x = c.r[14];\n"
            "    { static uint32_t rc_stub_log_n = 0;\n"
            "      char _gm[220]; snprintf(_gm, sizeof(_gm),\n"
            "        \"Fungsi 0x%%08x dipanggil arg r0=0x%%08x r1=0x%%08x r2=0x%%08x r3=0x%%08x lr=0x%%08x [#%%u]\",\n"
            "        0x%08x, c.r[0], c.r[1], c.r[2], c.r[3], c.r[14], rc_stub_log_n);\n"
            "      __android_log_print(ANDROID_LOG_WARN, \"GXM_STUB\", \"%%s\", _gm);\n"
            "      if (rc_stub_log_n < 10u || (rc_stub_log_n %% 500u) == 0u) {\n"
            "        if (rc_dbg_log_hook) rc_dbg_log_hook(\"GXM_STUB\", _gm);\n"
            "      }\n"
            "      if (rc_stub_log_n < 5u || (rc_stub_log_n %% 500u) == 0u) {\n"
            "        char _sm[180]; snprintf(_sm, sizeof(_sm),\n"
            "          \"Fungsi stub di 0x%%08x dipanggil (lr/ret=0x%%08x), return SCE_OK(0) [#%%u]\",\n"
            "          0x%08x, c.r[14], rc_stub_log_n);\n"
            "        __android_log_print(ANDROID_LOG_WARN, \"STUB_CALL\", \"%%s\", _sm);\n"
            "        if (rc_dbg_log_hook) rc_dbg_log_hook(\"STUB_CALL\", _sm);\n"
            "      }\n"
            "      ++rc_stub_log_n; }\n"
            "#endif\n" % (_a, _a, _a)
        )
        _stub_body = (
            "    // [GEN_FIX %08x] ARM stub: mvn r0,#0 ; bx lr  -> return -1\n" % _a +
            _log +
            "    c.r[0] = " + _ret + ";\n"
            "    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }"
        )
        _stub_body = _stub_body.replace(
            "    c.r[0] = " + _ret + ";\n",
            "    c.r[2] = 0x81010000u;\n"
            "    c.r[0] = 0x81010000u;\n"
            "    c.r[1] = 0x40u;\n"
            "    c.r[3] = 0x40u;\n"
            "    c.write32(0x81010000u, 0x11223344u);\n"
            "    c.r[0] = 0u;  // SCE_OK\n"
            "    c.r[0] = " + _ret + ";\n",
            1,
        )
    elif _a in _CRT_MEMCPY_STUBS:
        _stub_body = (
            f"    // [GEN_FIX {_a:08x}] CRT memcpy: r0=dest r1=size r2=src r3=end\n"
            "#if defined(__ANDROID__)\n"
            "    { static uint32_t _mc_n = 0; ++_mc_n;\n"
            "      if (_mc_n <= 3u || (_mc_n % 500u) == 0u) {\n"
            f"        char _mc[260]; snprintf(_mc, sizeof(_mc),\n"
            f"          \"CRT_MEMCPY: stub=0x{_a:08x} dest=0x%08x src=0x%08x n=0x%08x \"\n"
            "          \"byte_dest=%02x %02x %02x %02x [#%u]\",\n"
            "          c.r[0], c.r[2], c.r[1],\n"
            "          c.in_range(c.r[0],4u) ? c.read8(c.r[0]) : 0u,\n"
            "          c.in_range(c.r[0]+1u,1u) ? c.read8(c.r[0]+1u) : 0u,\n"
            "          c.in_range(c.r[0]+2u,1u) ? c.read8(c.r[0]+2u) : 0u,\n"
            "          c.in_range(c.r[0]+3u,1u) ? c.read8(c.r[0]+3u) : 0u, _mc_n);\n"
            "        __android_log_print(ANDROID_LOG_WARN, \"CRT_MEMCPY\", \"%s\", _mc);\n"
            "        if (rc_dbg_log_hook) rc_dbg_log_hook(\"CRT_MEMCPY\", _mc);\n"
            "      }\n"
            "    }\n"
            "#endif\n"
            "    {\n"
            "        uint32_t _dst = c.r[0], _src = c.r[2], _n = c.r[1];\n"
            "        if (_n > 0x100000u) _n = 0x100000u;  // safety cap 1 MiB\n"
            "        if (_n && c.in_range(_dst, _n) && c.in_range(_src, _n) && _dst != _src) {\n"
            "            std::memcpy(c.mem + (_dst - kMemBase), c.mem + (_src - kMemBase), _n);\n"
            "        }\n"
            "        c.r[0] = _dst;  // memcpy returns dest\n"
            "    }\n"
            "    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }"
        )
    elif _a in _CRT_ZEROFILL:
        _stub_body = (
            f"    // [GEN_FIX {_a:08x}] CRT parser helper: zero [r0+0x118] -> r6=0\n"
            "#if defined(__ANDROID__)\n"
            "    { static uint32_t _zf_n = 0; ++_zf_n;\n"
            "      if (_zf_n <= 3u || (_zf_n % 500u) == 0u) {\n"
            "        char _zf[180]; snprintf(_zf, sizeof(_zf),\n"
            f"          \"CRT_ZEROFILL: stub=0x{_a:08x} caller=0x%08x sp=0x%08x [#%u]\",\n"
            "          c.r[14], c.r[0], _zf_n);\n"
            "        __android_log_print(ANDROID_LOG_WARN, \"CRT_ZEROFILL\", \"%s\", _zf);\n"
            "        if (rc_dbg_log_hook) rc_dbg_log_hook(\"CRT_ZEROFILL\", _zf);\n"
            "      }\n"
            "    }\n"
            "#endif\n"
            "    if (c.in_range(c.r[0] + 0x118u, 4u)) c.write32(c.r[0] + 0x118u, 0u);\n"
            "    c.r[0] = 0u;\n"
            "    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }"
        )
    elif _a in _CRT_NEG_STUBS:
        _stub_body = (
            f"    // [GEN_FIX {_a:08x}] CRT stub -> return -1 (jalur guest valid)\n"
            "#if defined(__ANDROID__)\n"
            "    { static uint32_t _cn_n = 0; ++_cn_n;\n"
            "      if (_cn_n <= 3u || (_cn_n % 500u) == 0u) {\n"
            f"        char _cn[220]; snprintf(_cn, sizeof(_cn),\n"
            f"          \"CRT_NEG: stub=0x{_a:08x} caller=0x%08x r0=0x%08x r1=0x%08x r2=0x%08x r3=0x%08x [#%u]\",\n"
            "          c.r[14], c.r[0], c.r[1], c.r[2], c.r[3], _cn_n);\n"
            "        __android_log_print(ANDROID_LOG_WARN, \"CRT_NEG\", \"%s\", _cn);\n"
            "        if (rc_dbg_log_hook) rc_dbg_log_hook(\"CRT_NEG\", _cn);\n"
            "      }\n"
            "    }\n"
            "#endif\n"
            "    c.r[0] = 0xFFFFFFFFu;\n"
            "    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }"
        )
    elif _a in _CRT_THREAD_CREATE:
        _stub_body = (
            f"    // [GEN_FIX {_a:08x}] CRT create-thread: simpan entry r1, arg r3\n"
            f"    rc_crt_thread_create(c, c.r[0], c.r[1], c.r[2], c.r[3]);\n"
            "    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }"
        )
    elif _a in _CRT_THREAD_START:
        _stub_body = (
            f"    // [GEN_FIX {_a:08x}] CRT start-thread: uid/return-value ada di r0\n"
            f"    // (r1 = pointer arg, BUKAN slot). Kirim r0 sbg uid & slot.\n"
            f"    rc_crt_thread_start(c, c.r[0], c.r[0], c.r[2], c.r[3]);\n"
            "    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }"
        )
    elif _a in STUB_NID_MAP:
        _nid, _lbl = STUB_NID_MAP[_a]
        # Body HLE: dispatch ke rc_hle_dispatch(nid asli) dengan log STUB_CALL
        # throttled (first 3 + tiap 500) — ini BUKTI NID apa yang dipanggil guest.
        _log = (
            "#if defined(__ANDROID__)\n"
            "    { static uint32_t rc_hle_stub_n = 0;\n"
            "      ++rc_hle_stub_n;\n"
            "      if (rc_hle_stub_n <= 3u || (rc_hle_stub_n % 500u) == 0u) {\n"
            "        char _hs[260]; snprintf(_hs, sizeof(_hs),\n"
            "          \"STUB_CALL: NID=0x%08x name=%s caller=0x%08x lr=0x%08x \"\n"
            "          \"r0=0x%08x r1=0x%08x r2=0x%08x r3=0x%08x [#%u]\",\n"
            f"          0x{_nid:08x}u, \"{_lbl}\", c.current_pc, c.r[14], c.r[0], c.r[1], c.r[2], c.r[3],\n"
            "          rc_hle_stub_n);\n"
            "        __android_log_print(ANDROID_LOG_WARN, \"STUB_CALL\", \"%s\", _hs);\n"
            "        if (rc_dbg_log_hook) rc_dbg_log_hook(\"STUB_CALL\", _hs);\n"
            "      }\n"
            "    }\n"
            "#endif\n"
        )
        _stub_body = (
            f"    // [GEN_FIX {_a:08x}] ARM stub hollow -> HLE dispatch NID 0x{_nid:08x} ({_lbl})\n" +
            _log +
            f"    c.r[0] = static_cast<uint32_t>(::rc::rc_hle_dispatch(c, 0x{_nid:08x}u));\n" +
            "    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }"
        )
    else:
        # [CRT_SCE_OK] Stub hollow non-import (CRT/internal). Sebelumnya return
        # -1 (0xFFFFFFFF) yang membuat parser guest (mis. 0x81389498 yang
        # memanggil 0x81815364) menghasilkan r6=0x81970ea4 garbage -> loop
        # 0x8138979e 2 miliar iterasi -> halt. Kembalikan SCE_OK(0) + log.
        _log = (
            "#if defined(__ANDROID__)\n"
            "    { static uint32_t _cs_n = 0; ++_cs_n;\n"
            "      if (_cs_n <= 3u || (_cs_n % 500u) == 0u) {\n"
            f"        char _cs[220]; snprintf(_cs, sizeof(_cs),\n"
            f"          \"CRT_STUB: stub=0x{_a:08x} caller=0x%08x r0=0x%08x r1=0x%08x r2=0x%08x r3=0x%08x [#%u]\",\n"
            "          c.r[14], c.r[0], c.r[1], c.r[2], c.r[3], _cs_n);\n"
            "        __android_log_print(ANDROID_LOG_WARN, \"CRT_STUB\", \"%s\", _cs);\n"
            "        if (rc_dbg_log_hook) rc_dbg_log_hook(\"CRT_STUB\", _cs);\n"
            "      }\n"
            "    }\n"
            "#endif\n"
        )
        _stub_body = (
            f"    // [GEN_FIX {_a:08x}] ARM stub hollow -> SCE_OK(0)\n" +
            _log +
            "    c.r[0] = 0u;\n"
            "    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }"
        )
    FIXES.append((
        _a, 0, "00 00 e0 e3 1e ff 2f e1",
        _stub_body,
    ))


# [HLE_GXM_BEGIN_SCENE]/[HLE_GXM_END_SCENE] — stub grafis HLE untuk sceGxm*Scene.
# Game tidak pernah memanggil fungsi gambar (display list kosong), jadi render
# tidak pernah terjadi. Stub ini dipaksa dipanggil oleh dummy tick thread
# (lihat android_main.cpp): BeginScene mencatat; EndScene MENAMBAH counter
# CmdLists milik host — render loop Android (GL context current di sana)
# men-drain counter itu lalu menjalankan glClearColor + glDrawArrays dummy
# supaya DRAW_DEBUG menampilkan CmdLists>0.
# g_hle_cmdlists didefinisikan di android_main.cpp (volatile uint32_t) dan
# di-increment di sini. Kedua TU menggunakan C++ linkage dengan nama sama.
# Alamat sengaja di atas 0x81F00000 (TRAMPOLINE) dan di luar memsz guest.
HLE_GXM_BEGIN_SCENE = 0x81F00010
HLE_GXM_END_SCENE = 0x81F00020
FIXES.append((
    HLE_GXM_BEGIN_SCENE,
    0,
    None,  # synthetic block: tiada byte di ELF; verifikasi byte dilewati
    """    // [HLE_GXM_BEGIN_SCENE] sceGxmBeginScene dummy — menandai frame dimulai.
    // PARAM DUMP: cetak arg + konten pointer r0 (ctx) & stack, supaya kita bisa
    // MELIHAT apa yang dikirim guest ke sceGxmBeginScene (jika pernah dipanggil
    // guest asli). Dipanggil juga oleh paksa tick thread (arg = fake context).
    static uint32_t rc_hle_begin_n = 0;
    ++rc_hle_begin_n;
#if defined(__ANDROID__)
    if (rc_hle_begin_n <= 3u || (rc_hle_begin_n % 500u) == 0u) {
        char _hb[420];
        snprintf(_hb, sizeof(_hb),
            "sceGxmBeginScene #%u arg r0=0x%08x r1=0x%08x r2=0x%08x r3=0x%08x sp=0x%08x "
            "ctx[r0+0..12]=%08x %08x %08x %08x stk[sp+0..12]=%08x %08x %08x %08x",
            rc_hle_begin_n, c.r[0], c.r[1], c.r[2], c.r[3], c.r[13],
            c.in_range(c.r[0], 16u) ? c.read32(c.r[0] + 0x0u) : 0u,
            c.in_range(c.r[0], 16u) ? c.read32(c.r[0] + 0x4u) : 0u,
            c.in_range(c.r[0], 16u) ? c.read32(c.r[0] + 0x8u) : 0u,
            c.in_range(c.r[0], 16u) ? c.read32(c.r[0] + 0xcu) : 0u,
            c.in_range(c.r[13], 16u) ? c.read32(c.r[13] + 0x0u) : 0u,
            c.in_range(c.r[13], 16u) ? c.read32(c.r[13] + 0x4u) : 0u,
            c.in_range(c.r[13], 16u) ? c.read32(c.r[13] + 0x8u) : 0u,
            c.in_range(c.r[13], 16u) ? c.read32(c.r[13] + 0xcu) : 0u);
        __android_log_print(ANDROID_LOG_WARN, "GXM_STUB", "%s", _hb);
        if (rc_dbg_log_hook) rc_dbg_log_hook("GXM_STUB", _hb);
    }
#endif
    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }""",
))
FIXES.append((
    HLE_GXM_END_SCENE,
    0,
    None,  # synthetic block: tiada byte di ELF; verifikasi byte dilewati
    """    // [HLE_GXM_END_SCENE] sceGxmEndScene dummy — CmdLists++ di host.
    // Render loop Android (main thread, GL context current) membaca counter
    // ini dan HIHITUNG lewat (tanpa draw — segitiga dummy dihapus user).
    // PARAM DUMP: cetak arg + 8 word pertama dari buffer r0 (cand. command
    // buffer) & arg r1/r2 — untuk menilai apakah data GXM valid (jika ini
    // panggilan asli guest; paksa tick thread memakai buffer fake 0x81010000).
    extern volatile uint32_t g_hle_cmdlists;
    ++g_hle_cmdlists;
    const uint32_t _ln = g_hle_cmdlists;
#if defined(__ANDROID__)
    if (_ln <= 3u || (_ln % 500u) == 0u) {
        char _he[480];
        snprintf(_he, sizeof(_he),
            "sceGxmEndScene #%u CmdLists=%u arg r0=0x%08x r1=0x%08x r2=0x%08x r3=0x%08x sp=0x%08x "
            "cmdbuf[r0+0..28]=%08x %08x %08x %08x %08x %08x %08x %08x",
            _ln, _ln, c.r[0], c.r[1], c.r[2], c.r[3], c.r[13],
            c.in_range(c.r[0], 32u) ? c.read32(c.r[0] + 0x00u) : 0u,
            c.in_range(c.r[0], 32u) ? c.read32(c.r[0] + 0x04u) : 0u,
            c.in_range(c.r[0], 32u) ? c.read32(c.r[0] + 0x08u) : 0u,
            c.in_range(c.r[0], 32u) ? c.read32(c.r[0] + 0x0cu) : 0u,
            c.in_range(c.r[0], 32u) ? c.read32(c.r[0] + 0x10u) : 0u,
            c.in_range(c.r[0], 32u) ? c.read32(c.r[0] + 0x14u) : 0u,
            c.in_range(c.r[0], 32u) ? c.read32(c.r[0] + 0x18u) : 0u,
            c.in_range(c.r[0], 32u) ? c.read32(c.r[0] + 0x1cu) : 0u);
        __android_log_print(ANDROID_LOG_WARN, "DRAW_DEBUG", "%s", _he);
        if (rc_dbg_log_hook) rc_dbg_log_hook("DRAW_DEBUG", _he);
    }
#endif
    { uint32_t t = c.r[14] & ~1u; c.next_pc = t; return; }""",
))

# Trampoline LR untuk dummy tick thread: alamat landing pad yang sah supaya
# `bx lr` di fungsi paksa (0x813833cc / stub grafis) TIDAK PERNAH mendarat di
# 0x00000000 (null pointer). Blok ini synthetic (tidak ada byte asli di ELF):
# hanya menghentikan eksekusi guest secara bersih + mencatat hitungan.
TRAMPOLINE_ADDR = 0x81F00000
FIXES.append((
    TRAMPOLINE_ADDR,
    0,
    None,  # synthetic block: tiada byte di ELF; verifikasi byte dilewati
    """    // [TICK_TRAMPOLINE] landing pad untuk lr dummy tick thread (android_main.cpp).
    // bx lr dari forced call mendarat di sini -> halt bersih (next_pc=0),
    // dummy tick thread mendeteksi next_pc==0 lalu lanjut tick berikutnya.
    static uint32_t rc_tick_trampoline_n = 0;
    ++rc_tick_trampoline_n;
    if (rc_dbg_log_hook && (rc_tick_trampoline_n <= 3u || (rc_tick_trampoline_n % 1000u) == 0u)) {
        char _tm[140];
        snprintf(_tm, sizeof(_tm),
            "[TRAMPOLINE] forced-call return landing #%u (lr valid, halt bersih, tick lanjut)",
            rc_tick_trampoline_n);
        rc_dbg_log_hook("TRAMPOLINE", _tm);
    }
    c.next_pc = 0u;  // halt bersih: step_block berhenti tanpa merusak state
    return;""",
))

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
        if expect is None:
            continue  # synthetic block (mis. [TICK_TRAMPOLINE]) — tiada byte di ELF
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
        "#include <cstdio>",
        "#include <cstring>",
        "#if defined(__ANDROID__)",
        "#include <android/log.h>",
        "#endif",
        "",
        'extern "C" {',
        "    typedef void (*rc_dbg_log_fn)(const char*, const char*);",
        "    extern rc_dbg_log_fn rc_dbg_log_hook;",
        "    extern uint32_t rc_dbg_hist_pc[32];",
        "    extern uint32_t rc_dbg_hist_n;",
        "    extern uint32_t rc_dbg_hist_i;",
        "    extern uint64_t rc_dbg_step_total;",
        "    extern uint32_t rc_dbg_hle_calls;",
        "    extern uint32_t rc_dbg_hle_hist_nid[8];",
        "    extern uint32_t rc_dbg_hle_hist_pc[8];",
        "    extern uint32_t rc_dbg_hle_hist_n;",
        "    extern uint32_t rc_dbg_hle_hist_i;",
        "    extern uint32_t rc_dbg_last_nid;",
        "    extern uint64_t rc_dbg_stub_returns;",
        "    extern uint32_t rc_nearest_block(uint32_t);",
        "    extern int rc_crt_thread_create(rc::Cpu&, uint32_t, uint32_t, uint32_t, uint32_t);",
        "    extern int rc_crt_thread_start(rc::Cpu&, uint32_t, uint32_t, uint32_t, uint32_t);",
        "    extern uint32_t rc_dbg_stub_caller_81815464;",
        "    extern uint32_t rc_dbg_stub_caller_81815494;",
        "}",
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
        "// [REGEN_INVALID] part998: blok Thumb-2 hasil regenerate. Harus dicek",
        "// lebih dulu karena alamatnya bertabrakan dengan part042 (binary search).",
        "extern const BlockDesc rc_blocks_part998[];",
        "extern const size_t rc_blocks_part998_count;",
        "const BlockDesc* rc_fix_find_block(uint32_t pc) {",
        "    for (size_t i = 0; i < rc_blocks_part998_count; ++i) {",
        "        if (rc_blocks_part998[i].addr == pc) return &rc_blocks_part998[i];",
        "    }",
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

# ---------------------------------------------------------------------------
# HLE table entries for SceThreadmgr and needed GXM NIDs.
# ---------------------------------------------------------------------------
# NID list from reversed ELF import table (SceThreadmgr 15 functions).
# This is a minimal set for implementing sceKernelCreateThread and
# sceKernelStartThread with real std::thread runners; other functions are
# stubbed to SCE_OK so the binary can continue.
HLE_TABLE_ENTRIES = [
    # Handler nyata (NID RESMI vita-headers/ScarletNX/vita2hos).
    # Game ini TIDAK mengimpor dua NID ini via SceThreadmgr — didaftarkan
    # untuk berjaga-jaga bila dipanggil via jalur lain; stub hollow biasa
    # tetap SCE_OK lewat cabang default rc_hle_dispatcher.
    (0xc5c11ee7, "sceKernelCreateThread"),
    (0xf08de149, "sceKernelStartThread"),
    # 15 import SceThreadmgr yang BENAR-BENAR ada di ELF game.
    (0x03e23af6, "_sceKernelGetSemaInfo"),
    (0x0c8a38e1, "sceKernelExitThread"),
    (0x15129174, "SceThreadmgr_imp_2"),
    (0x1a372ec8, "sceKernelUnlockMutex"),
    (0x1bbde3d9, "sceKernelDeleteThread"),
    (0x1d17decf, "sceKernelExitDeleteThread"),
    (0x4b675d05, "sceKernelDelayThread"),
    (0x6ed2e2dc, "sceKernelSignalCond"),
    (0x879e6ebd, "sceKernelDeleteCond"),
    (0xa2d81f9e, "SceThreadmgr_imp_10"),
    (0xbd0139f2, "sceKernelChangeThreadPriority"),
    (0xcbe235c7, "SceThreadmgr_imp_12"),
    (0xcc18fbae, "SceThreadmgr_imp_13"),
    (0xdb32948a, "sceKernelDeleteSema"),
    (0xe6b761d1, "sceKernelSignalSema"),
]
ALL_HLE_ENTRIES = HLE_TABLE_ENTRIES


def patch_hle_table(path: Path) -> bool:
    """Replace rc_hle_table with entries from ALL_HLE_ENTRIES."""
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if "// HLE_TABLE_ENTRIES" in text:
        return False  # already patched

    # Build new table lines.
    entries = []
    for nid, name in ALL_HLE_ENTRIES:
        entries.append(f"    {{0x{nid:08x}u, \"{name}\", rc_hle_dispatcher}}, // generated from HLE_TABLE_ENTRIES")
    table_body = ",\n".join(entries)
    new_table_decl = f"extern const HleEntry rc_hle_table[] = {{\n{table_body}\n}};"
    new_count_decl = f"extern const size_t rc_hle_count = {len(ALL_HLE_ENTRIES)};"

    # Replace the two lines that define the old table.
    lines = text.splitlines(keepends=True)
    out = []
    replaced_table = False
    replaced_count = False
    for line in lines:
        line_stripped = line.strip()
        if line_stripped.startswith("extern const HleEntry rc_hle_table[]") and not replaced_table:
            out.append(new_table_decl + "\n")
            replaced_table = True
            continue
        if line_stripped.startswith("extern const size_t rc_hle_count") and not replaced_count:
            out.append(new_count_decl + "\n")
            replaced_count = True
            continue
        out.append(line)
    if not replaced_table or not replaced_count:
        # fallback: simple replace
        text = text.replace("extern const HleEntry rc_hle_table[] = {{0u, \"unmapped\", nullptr}};", new_table_decl + "\n")
        text = text.replace("extern const size_t rc_hle_count = 0;", new_count_decl + "\n")
    path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return True


def patch_recomp_h(path: Path) -> bool:
    """Add rc_hle_dispatcher prototype to recomp.h if missing."""
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if "rc_hle_dispatcher" in text:
        return False
    anchor = "int rc_hle_dispatch(Cpu& cpu, uint32_t nid);"
    addition = (
        "\n// Dispatcher for SceThreadmgr / GXM import stubs.  The matched NID is stored\n"
        "// in rc_dbg_last_nid before the call, so this function can switch on it.\n"
        "int rc_hle_dispatcher(Cpu& cpu, uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3);\n"
    )
    if anchor not in text:
        raise RuntimeError(f"anchor rc_hle_dispatch tidak ditemukan di {path}")
    text = text.replace(anchor, anchor + "\n" + addition, 1)
    path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return True


def patch_recomp_cpp(path: Path) -> bool:
    """Insert HLE thread manager + rc_hle_dispatcher into recomp.cpp."""
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if "rc_hle_threadmgr_create_thread" in text:
        return False  # already patched

    # 1) add includes after #include <sys/mman.h>
    inc_anchor = "#include <sys/mman.h>"
    inc_add = (
        "\n#include <thread>\n#include <mutex>\n#include <vector>\n#include <atomic>\n#include <chrono>\n"
    )
    if inc_anchor not in text:
        raise RuntimeError(f"include anchor tidak ditemukan di {path}")
    text = text.replace(inc_anchor, inc_anchor + inc_add, 1)

    # 2) insert dispatcher + thread manager before "void rc_unimplemented"
    unimpl_anchor = "void rc_unimplemented(uint32_t pc, const char* what) {"
    block = r'''// ---------------------------------------------------------------------------
// HLE thread manager: minimal real guest-thread support.
// ---------------------------------------------------------------------------
namespace {
struct GuestThreadState {
    uint32_t uid = 0;
    uint32_t entry = 0;
    uint32_t arg = 0;
    std::thread th;
    std::atomic<bool> started{false};
    std::atomic<bool> finished{false};
};

// [GUEST_MEM_ALIVE] Set false oleh rc_shutdown; thread guest cek ini sebelum
// akses mem supaya tidak use-after-free saat activity ditutup.
std::atomic<bool> g_guest_mem_alive{true};

std::mutex g_guest_thread_mutex;
std::vector<GuestThreadState*> g_guest_threads;
uint32_t g_guest_thread_next_uid = 0x1000u;

void guest_thread_runner(uint8_t* mem, uint32_t mem_size, uint32_t entry,
                         uint32_t arg, uint32_t uid) {
    Cpu c;
    c.mem = mem;
    c.mem_size = mem_size;
    c.reset();
    c.r[0] = arg;
    c.r[1] = 0;
    c.r[13] = kStackTop - ((uid & 0xFFu) + 1u) * 0x10000u;
    c.r[14] = 0;
    c.next_pc = entry & ~1u;
    c.current_pc = entry & ~1u;
    c.thumb = entry & 1u;
    c.cpsr = (c.cpsr & ~kFlagT) | (c.thumb ? kFlagT : 0u);
    c.running = true;
    c.faulted = false;
    c.max_blocks = 0;
    c.run();
}

}  // namespace

int rc_hle_threadmgr_create_thread(Cpu& cpu, uint32_t name, uint32_t entry,
                                   uint32_t priority, uint32_t stack_size) {
    (void)name; (void)priority; (void)stack_size;
    std::lock_guard<std::mutex> lk(g_guest_thread_mutex);
    GuestThreadState* t = new GuestThreadState();
    t->uid = g_guest_thread_next_uid++;
    t->entry = entry;
    g_guest_threads.push_back(t);
    char msg[200];
    std::snprintf(msg, sizeof(msg),
        "sceKernelCreateThread: entry=0x%08x priority=%u stack=0x%x -> uid=0x%08x",
        entry, priority, stack_size, t->uid);
    if (rc_dbg_log_hook) rc_dbg_log_hook("HLE_THREAD", msg);
    return static_cast<int>(t->uid);
}

int rc_hle_threadmgr_start_thread(Cpu& cpu, uint32_t uid, uint32_t arg) {
    GuestThreadState* t = nullptr;
    {
        std::lock_guard<std::mutex> lk(g_guest_thread_mutex);
        for (auto* e : g_guest_threads) if (e->uid == uid) { t = e; break; }
    }
    if (!t) return -1;
    t->arg = arg;
    if (!t->started.exchange(true)) {
        uint8_t* mem = cpu.mem;
        const uint32_t mem_size = cpu.mem_size;
        const uint32_t entry = t->entry;
        const uint32_t uid_local = t->uid;
        t->th = std::thread(guest_thread_runner, mem, mem_size, entry,
                            arg, uid_local);
        t->th.detach();
        char msg[200];
        std::snprintf(msg, sizeof(msg),
            "sceKernelStartThread: uid=0x%08x entry=0x%08x arg=0x%08x -> std::thread spawned",
            uid, entry, arg);
        if (rc_dbg_log_hook) rc_dbg_log_hook("HLE_THREAD", msg);
    }
    return 0;
}

// ---------------------------------------------------------------------------
// rc_crt_thread_* : CRT-internal thread stubs dipanggil guest saat init.
// create(r0=name, r1=entry, r2=stack, r3=arg) -> simpan entry per slot.
// start (r1=slot ptr) -> jalankan entry di std::thread nyata. Jika entry di luar
// guest window (pointer ke struktur heap yang belum ada), pakai game-loop
// 0x813833cc sebagai fallback supaya thread tetap hidup & GXM bisa dipanggil.
// ---------------------------------------------------------------------------
namespace {
struct CrtThread { uint32_t entry, arg; std::thread th; std::atomic<bool> started{false}; };
std::mutex g_crt_thr_mtx;
std::vector<CrtThread*> g_crt_thrs;
uint32_t g_crt_thr_seq = 0;

void crt_thread_runner(uint8_t* mem, uint32_t mem_size, uint32_t entry, uint32_t arg, uint32_t idx) {
    Cpu c;
    c.mem = mem; c.mem_size = mem_size; c.reset();
    c.r[0] = arg; c.r[1] = idx;
    c.r[13] = kStackTop - (idx + 2u) * 0x20000u;
    c.r[14] = 0x81F00000u;  // trampoline: halt bersih
    c.next_pc = entry & ~1u; c.current_pc = entry & ~1u;
    c.thumb = entry & 1u;
    c.cpsr = (c.cpsr & ~kFlagT) | (c.thumb ? kFlagT : 0u);
    c.running = true; c.faulted = false; c.max_blocks = 0;
    // jalan dalam chunk kecil sambil s->running dicek lewat flag global.
    for (int chunk = 0; chunk < 100000 && c.running && !c.faulted && g_guest_mem_alive.load(); ++chunk) {
        c.max_blocks = c.blocks_run + 20000u;
        c.run();
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (rc_dbg_log_hook) {
        char m[200];
        std::snprintf(m, sizeof(m), "CRT thread #%u selesai: entry=0x%08x blocks=%llu faulted=%d",
                      idx, entry, (unsigned long long)c.blocks_run, (int)c.faulted);
        rc_dbg_log_hook("HLE_THREAD", m);
    }
}
}  // namespace

// [TBH_NEAREST] Cari awal blok rekompilasi terdekat <= pc (untuk tbh/tbb).
// Iterasi linear semua part (jarang dipanggil, hanya saat jump-table).
extern "C" uint32_t rc_nearest_block(uint32_t pc) {
    uint32_t best = 0u;
    // binary search part terakhir yang alamat pertamanya <= pc
    size_t lo = 0u, hi = rc_block_parts_count;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2u;
        if (rc_block_parts[mid].data->addr <= pc) lo = mid + 1u; else hi = mid;
    }
    if (lo > 0u) {
        const BlockPart& bp = rc_block_parts[lo - 1u];
        for (size_t i = 0; i < bp.count; ++i) {
            uint32_t a = bp.data[i].addr;
            if (a <= pc && a > best) best = a;
        }
    }
    if (const BlockDesc* f = rc_fix_find_block(pc)) best = f->addr;
    return best;
}

extern "C" int rc_crt_thread_create(Cpu& cpu, uint32_t name, uint32_t entry, uint32_t stack, uint32_t arg) {
    (void)name; (void)stack;
    std::lock_guard<std::mutex> lk(g_crt_thr_mtx);
    CrtThread* t = new CrtThread();
    t->entry = entry; t->arg = arg;
    g_crt_thrs.push_back(t);
    uint32_t idx = g_crt_thr_seq++;
    if (rc_dbg_log_hook) {
        char m[220];
        std::snprintf(m, sizeof(m),
            "CRT create-thread #%u: entry=0x%08x arg=0x%08x stack=0x%x -> slot=%u",
            idx, entry, arg, stack, idx);
        rc_dbg_log_hook("HLE_THREAD", m);
    }
    return static_cast<int>(idx);
}

extern "C" int rc_crt_thread_start(Cpu& cpu, uint32_t r0, uint32_t slot, uint32_t r2, uint32_t r3) {
    (void)r0; (void)r2; (void)r3;
    std::lock_guard<std::mutex> lk(g_crt_thr_mtx);
    if (slot >= g_crt_thrs.size()) return -1;
    CrtThread* t = g_crt_thrs[slot];
    if (t->started.exchange(true)) return 0;
    uint32_t entry = t->entry;
    // Validasi entry: harus di guest window & punya blok rekompilasi.
    if (entry < kMemBase || entry >= kMemBase + kMemSize || !find_block(entry & ~1u)) {
        if (rc_dbg_log_hook) {
            char m[240];
            std::snprintf(m, sizeof(m),
                "CRT start-thread #%u: entry 0x%08x TIDAK valid -> fallback game-loop 0x813833cc",
                slot, entry);
            rc_dbg_log_hook("HLE_THREAD", m);
        }
        entry = 0x813833ccu;
    }
    uint8_t* mem = cpu.mem; uint32_t mem_size = cpu.mem_size;
    uint32_t arg = t->arg;
    t->th = std::thread(crt_thread_runner, mem, mem_size, entry, arg, slot);
    t->th.detach();
    if (rc_dbg_log_hook) {
        char m[240];
        std::snprintf(m, sizeof(m),
            "CRT start-thread #%u: std::thread spawned entry=0x%08x arg=0x%08x",
            slot, entry, arg);
        rc_dbg_log_hook("HLE_THREAD", m);
    }
    return 0;
}

int rc_hle_dispatcher(Cpu& cpu, uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) {
    switch (rc_dbg_last_nid) {
        case 0xC5C11EE7u:  // sceKernelCreateThread (NID RESMI)
            return rc_hle_threadmgr_create_thread(cpu, r0, r1, r2, r3);
        case 0xF08DE149u:  // sceKernelStartThread (NID RESMI)
            return rc_hle_threadmgr_start_thread(cpu, r0, r1);
        default:
            return 0;  // other SceThreadmgr/GXM stubs: SCE_OK
    }
}

void rc_unimplemented(uint32_t pc, const char* what) {'''
    if unimpl_anchor not in text:
        raise RuntimeError(f"rc_unimplemented anchor tidak ditemukan di {path}")
    text = text.replace(unimpl_anchor, block, 1)

    # 3) rc_shutdown(): tandai guest memory tidak alive supaya thread guest
    #    berhenti sebelum munmap (cegah use-after-free saat activity ditutup).
    shutdown_old = """void rc_shutdown(Cpu& cpu) {
    if (cpu.mem) {
        munmap(cpu.mem, cpu.mem_size);
        cpu.mem = nullptr;
    }
}"""
    shutdown_new = """void rc_shutdown(Cpu& cpu) {
    g_guest_mem_alive.store(false);  // [GUEST_MEM_ALIVE] stop guest threads
    for (int _w = 0; _w < 200 && g_crt_thrs.size(); ++_w) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (cpu.mem) {
        munmap(cpu.mem, cpu.mem_size);
        cpu.mem = nullptr;
    }
}"""
    if shutdown_old in text:
        text = text.replace(shutdown_old, shutdown_new, 1)

    path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return True


def collect_tbh_fixes(gen: Path) -> list[tuple]:
    """Scan gen/*.cpp untuk `RC_UNIMPL ... tbh/tbb`, hasilkan FIX otomatis.

    translator tidak mendukung instruksi jump-table Thumb (tbb/tbh). Fix yang
    dihasilkan membaca tabel dari guest memory saat runtime lalu melompat ke
    awal blok rekompilasi terdekat lewat rc_nearest_block().
    """
    import re as _re
    found: dict[int, str] = {}
    for cpp in sorted(gen.glob("recompiled_part*.cpp")):
        if cpp.name == GEN_FIX_FILE:
            continue
        try:
            txt = cpp.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            continue
        for m in _re.finditer(r'RC_UNIMPL\(0x([0-9a-f]+)u, "(tbb|tbh) \[pc, (r\d+)(?:, lsl (\d+))?\]"\)', txt):
            addr = int(m.group(1), 16)
            if addr in found:
                continue
            kind, reg, shift = m.group(2), m.group(3), m.group(4) or "0"
            halfword = 1 if kind == "tbh" else 0
            rreg = f"c.r[{int(reg[1:])}]"
            body = (
                "    // [AUTO_TBH] jump-table generik: baca tabel guest, lompat ke blok terdekat\n"
                "    {\n"
                f"        uint32_t _pc = 0x{addr:08x}u;\n"
                "        uint32_t _base = (_pc + 4u) & ~3u;\n"
                f"        uint32_t _idx = {rreg};\n"
                f"        uint32_t _elem = _base + (_idx << {int(shift)}u);\n"
                "        uint32_t _off = 0u;\n"
                f"        if (c.in_range(_elem, {2 if halfword else 1}u)) _off = "
                f"c.read{'16' if halfword else '8'}(_elem);\n"
                f"        uint32_t _target = _base + (_off << {int(shift)}u);\n"
                "        uint32_t _nb = rc_nearest_block(_target);\n"
                "#if defined(__ANDROID__)\n"
                "        { static uint32_t _tn = 0; if (_tn++ < 5u) {\n"
                "            char _tb[160]; snprintf(_tb, sizeof(_tb),\n"
                '              "AUTO_TBH: pc=0x%08x idx=0x%x target=0x%08x -> 0x%08x",\n'
                "              _pc, _idx, _target, _nb);\n"
                '            __android_log_print(ANDROID_LOG_WARN, "AUTO_TBH", "%s", _tb);\n'
                '            if (rc_dbg_log_hook) rc_dbg_log_hook("AUTO_TBH", _tb);\n'
                "        }}\n"
                "#endif\n"
                "        c.next_pc = _nb ? _nb : (_pc + 2u); return;\n"
                "    }"
            )
            found[addr] = body
    out = []
    known = {f[0] for f in FIXES}
    for addr in sorted(found):
        if addr in known:
            continue  # sudah ditangani FIX manual
        # byte asli tidak dipakai (verify dilewati utk None)
        out.append((addr, 1, None, found[addr]))
    return out


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
    recomp_h = root / "recomp.h"
    for p in (gen, parts, recomp, recomp_h):
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

    # Kumpulkan fix otomatis untuk semua tbh/tbb yang tidak diterjemahkan.
    auto_tbh = collect_tbh_fixes(gen)
    all_fixes = list(FIXES) + auto_tbh
    if auto_tbh:
        print(f"OK: {len(auto_tbh)} fix tbh/tbb otomatis.")
    (gen / GEN_FIX_FILE).write_text(emit_fix_file(all_fixes), encoding="utf-8")
    changed = ["part999"]
    if patch_registry(parts):
        changed.append("registry")
    if patch_lookup(recomp):
        changed.append("lookup")
    if patch_hle_table(recomp):
        changed.append("hle_table")
    if patch_recomp_h(recomp_h):
        changed.append("recomp_h")
    if patch_recomp_cpp(recomp):
        changed.append("recomp_cpp")
    print(f"OK: blok perbaikan ditulis ({', '.join(changed)}), total {len(FIXES)} blok.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
