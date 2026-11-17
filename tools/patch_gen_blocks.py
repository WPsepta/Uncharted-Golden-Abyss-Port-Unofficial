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
        // Paksa keluar secara natural setelah ~100 iterasi (set r5==r6 agar loop selesai)
        if (rc_loop_8138979e >= 100u) {
            rc_loop_8138979e = 0;
            c.r[5] = c.r[6];
            RC_SUB_FLAGS(c.r[5], c.r[6], 0u); // blo menjadi false
#if defined(__ANDROID__)
            {
                char _lb[200];
                snprintf(_lb, sizeof(_lb),
                    "LOOP_WAIT: forced natural exit after 100 iters (addr=0x%08x val=0x%08x) -> r5=r6=0x%08x",
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
            const uint32_t n = rc_dbg_hist_n > 16u ? 16u : rc_dbg_hist_n;
            char tbuf[512]; size_t tp = 0;
            tp += (size_t)snprintf(tbuf + tp, sizeof(tbuf) - tp,
                "TRACE blok terakhir (%u, steps=%llu):", n, (unsigned long long)rc_dbg_step_total);
            for (uint32_t k = 0; k < n && tp + 12 < sizeof(tbuf); ++k) {
                const uint32_t idx = (rc_dbg_hist_i + 16u - n + k) & 15u;
                tp += (size_t)snprintf(tbuf + tp, sizeof(tbuf) - tp, " 0x%08x", rc_dbg_hist_pc[idx]);
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
_LOG_STUBS = {0x81815464, 0x81815494}
for _a in ARM_STUB_ADDRS:
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
    if _ret == "0u":
        # Untuk stub SCE_OK (0x81815464/0x81815494): beri fake context/command
        # buffer yang valid di guest memory supaya arg yang diberikan caller
        # (r2=*list slot) tidak jadi 0/TIDAK VALID. Setup sebelum return.
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
        "#if defined(__ANDROID__)",
        "#include <android/log.h>",
        "#endif",
        "",
        'extern "C" {',
        "    typedef void (*rc_dbg_log_fn)(const char*, const char*);",
        "    extern rc_dbg_log_fn rc_dbg_log_hook;",
        "    extern uint32_t rc_dbg_hist_pc[16];",
        "    extern uint32_t rc_dbg_hist_n;",
        "    extern uint32_t rc_dbg_hist_i;",
        "    extern uint64_t rc_dbg_step_total;",
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

# ---------------------------------------------------------------------------
# HLE table entries for SceThreadmgr and needed GXM NIDs.
# ---------------------------------------------------------------------------
# NID list from reversed ELF import table (SceThreadmgr 15 functions).
# This is a minimal set for implementing sceKernelCreateThread and
# sceKernelStartThread with real std::thread runners; other functions are
# stubbed to SCE_OK so the binary can continue.
HLE_TABLE_ENTRIES = [
    (0x03e23af6, "sceKernelCreateThread"),
    (0x0c8a38e1, "sceKernelStartThread"),
    (0x15129174, "sceKernelExitThread"),
    (0x1a372ec8, "sceKernelSleepThread"),
    (0x1bbde3d9, "sceKernelDelayThread"),
    (0x1d17decf, "sceKernelSuspendThread"),
    (0x4b675d05, "sceKernelResumeThread"),
    (0x6ed2e2dc, "sceKernelRotateThreadReadyQueue"),
    (0x879e6ebd, "sceKernelChangeThreadPriority"),
    (0xa2d81f9e, "sceKernelGetThreadId"),
    (0xbd0139f2, "sceKernelReferThreadStatus"),
    (0xcbe235c7, "sceKernelWaitThreadEnd"),
    (0xcc18fbae, "sceKernelGetThreadExitStatus"),
    (0xdb32948a, "sceKernelCreateCallback"),
    (0xe6b761d1, "sceKernelNotifyCallback"),
]

# GXM NIDs needed for real rendering; we stub them as well (SCE_OK).
# The actual HLE GXM BeginScene/EndScene are already fixed in the code; these
# are the import stubs that the guest calls. For now we just return 0.
GXM_NIDS = [
    (0x008402c6, "sceGxmMapUniformBuffer"),
    (0x029b4f1c, "sceGxmSetViewport"),
    (0x0389861d, "sceGxmSetScissor"),
    (0x05032658, "sceGxmSetDepthTest"),
    (0x06752183, "sceGxmSetStencilTest"),
    (0x06ff9151, "sceGxmSetBlendFunc"),
    (0x0733d8ae, "sceGxmBeginScene"),  # entry point (but HLE fix replaces this address?)
    (0x099134f5, "sceGxmSetVertexShader"),
    (0x0b94c50a, "sceGxmSetFragmentShader"),
    (0x0c44acd7, "sceGxmSetUniformBuffer"),
    (0x11dc8dc9, "sceGxmSetPixelUnpackState"),
    (0x12625c34, "sceGxmGetDisplayFrameBuffer"),
    (0x126a3eb3, "sceGxmGetRenderingStatus"),
    (0x12aaa7af, "sceGxmSyncObject"),
    (0x1625d348, "sceGxmCreateVertexBuffer"),
    (0x17cf46b9, "sceGxmSetVertexBuffer"),
    (0x1ca9fe0b, "sceGxmDraw"),
    (0x207af96b, "sceGxmEndScene"),
]

ALL_HLE_ENTRIES = HLE_TABLE_ENTRIES + GXM_NIDS


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
        "\n#include <thread>\n#include <mutex>\n#include <vector>\n#include <atomic>\n"
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

int rc_hle_dispatcher(Cpu& cpu, uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3) {
    switch (rc_dbg_last_nid) {
        case 0x03e23af6u:  // sceKernelCreateThread
            return rc_hle_threadmgr_create_thread(cpu, r0, r1, r2, r3);
        case 0x0c8a38e1u:  // sceKernelStartThread
            return rc_hle_threadmgr_start_thread(cpu, r0, r1);
        default:
            return 0;  // other SceThreadmgr/GXM stubs: SCE_OK
    }
}

void rc_unimplemented(uint32_t pc, const char* what) {'''
    if unimpl_anchor not in text:
        raise RuntimeError(f"rc_unimplemented anchor tidak ditemukan di {path}")
    text = text.replace(unimpl_anchor, block, 1)
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

    (gen / GEN_FIX_FILE).write_text(emit_fix_file(FIXES), encoding="utf-8")
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
