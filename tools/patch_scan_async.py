#!/usr/bin/env python3
"""patch_scan_async.py — jalankan asset scan di worker thread (anti-ANR).

Masalah: `scan_assets()` mengiterasi 28.336 file + ratusan `overlay_logf`
(fopen/fflush/fclose) di main/UI thread. Android membunyikan ANR bila main
thread blok >5 detik — itulah sebabnya dialog "tidak merespons" muncul tiap
~10 detik dan log selalu terpotong di titik yang sama.

Solusi: jalankan scan lewat `std::async(std::launch::async, ...)`. Main thread
terus memompa `ALooper_pollOnce` sambil menunggu future siap. Begitu scan
selesai, hasil diambil dan log dicetak seperti biasa.

Pemakaian: python3 tools/patch_scan_async.py <app/src/main/cpp>
"""
from __future__ import annotations

import sys
from pathlib import Path

MARK = "[SCAN_ASYNC]"
INC_ANCHOR = "#include <thread>"
BLOCK_START = (
    "    // Scan rekursif (termasuk sub-folder seperti extracted/) — sinkron agar\n"
    "    // keputusan render loop berdasarkan hasil nyata, bukan hang di loop hitam.\n"
    "    const std::string dir = asset_dir;\n"
    "    AssetCensus c = scan_assets(dir.c_str());   // recursive_directory_iterator\n"
    "    g_btex_count = c.btex;\n"
)
BLOCK_END = (
    "    while (!state.running.load()) {\n"
    "        int events = 0;\n"
    "        android_poll_source* source = nullptr;\n"
    "        while (ALooper_pollOnce(1, nullptr, &events, reinterpret_cast<void**>(&source)) >= 0) {\n"
    "            if (source) source->process(app, source);\n"
    "            if (app->destroyRequested) return;\n"
    "        }\n"
    "    }\n"
)

NEW_BLOCK = (
    "    // [SCAN_ASYNC] Scan di worker thread — main thread terus pump events\n"
    "    // (Android ANR kalau main thread blok >5 detik).\n"
    "    const std::string dir = asset_dir;\n"
    "    std::future<AssetCensus> scan_future =\n"
    "        std::async(std::launch::async, scan_assets, dir.c_str());\n"
    "    AssetCensus c;\n"
    "    bool scan_ready = false;\n"
    "    auto finish_scan = [&]() {\n"
    "        if (scan_ready) return;\n"
    "        c = scan_future.get();\n"
    "        scan_ready = true;\n"
    "        g_btex_count = c.btex;\n"
    "        ui::overlay_logf(\"asset census @ %s (recursive)\", dir.c_str());\n"
    "        ui::overlay_logf(\"  xvag=%llu  btex=%llu  bsm=%llu  bsanim=%llu  hkx=%llu\",\n"
    "                         (unsigned long long)c.xvag, (unsigned long long)c.btex,\n"
    "                         (unsigned long long)c.bsm, (unsigned long long)c.bsanim,\n"
    "                         (unsigned long long)c.hkx);\n"
    "        ui::overlay_logf(\"  bsp=%llu  bnk=%llu  bsfnt=%llu  png=%llu  csv=%llu  dll=%llu  other=%llu\",\n"
    "                         (unsigned long long)c.bsp, (unsigned long long)c.bnk,\n"
    "                         (unsigned long long)c.bsfnt, (unsigned long long)c.png,\n"
    "                         (unsigned long long)c.csv, (unsigned long long)c.dll,\n"
    "                         (unsigned long long)c.other);\n"
    "        ui::overlay_logf(\"  total=%llu  errors=%llu\",\n"
    "                         (unsigned long long)c.total, (unsigned long long)c.errors);\n"
    "    };\n"
    "\n"
    "    while (!state.running.load() || !scan_ready) {\n"
    "        int events = 0;\n"
    "        android_poll_source* source = nullptr;\n"
    "        while (ALooper_pollOnce(1, nullptr, &events, reinterpret_cast<void**>(&source)) >= 0) {\n"
    "            if (source) source->process(app, source);\n"
    "            if (app->destroyRequested) return;\n"
    "        }\n"
    "        if (!scan_ready && scan_future.valid() &&\n"
    "            scan_future.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready) {\n"
    "            finish_scan();\n"
    "        }\n"
    "    }\n"
    "    if (!scan_ready) finish_scan();  // window init lebih dulu daripada scan\n"
    "\n"
    "    if (c.btex == 0 && c.bsm == 0) {\n"
    "        ui::overlay_logf(\"ERROR: aset game tidak ditemukan (btex=0, bsm=0). \"\n"
    "                         \"Cek path dan isi folder (termasuk sub-folder). \"\n"
    "                         \"Kembali ke launcher.\");\n"
    "        ANativeActivity_finish(app->activity);\n"
    "        return;\n"
    "    }\n"
    "#if RC_WITH_GAME_CODE\n"
    "    ui::overlay_logf(\"build mode: FULL (gen/ parts ditemukan)\");\n"
    "#else\n"
    "    ui::overlay_logf(\"build mode: STUB (gen/ parts tidak ada — lihat README)\");\n"
    "#endif\n"
)


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    if len(args) != 1:
        print("pemakaian: patch_scan_async.py <app/src/main/cpp>", file=sys.stderr)
        return 2
    p = Path(args[0]) / "android_main.cpp"
    text = p.read_text(encoding="utf-8", errors="surrogateescape")
    if MARK in text:
        print("OK: sudah terpatch.")
        return 0

    # 1) include <future>
    if INC_ANCHOR not in text:
        raise RuntimeError("anchor include <thread> tidak ditemukan")
    text = text.replace(INC_ANCHOR, INC_ANCHOR + "\n#include <future>", 1)

    # 2) ganti blok scan sinkron s/d event loop dengan versi async
    if BLOCK_START not in text:
        raise RuntimeError("BLOCK_START tidak ditemukan")
    i = text.index(BLOCK_START)
    j = text.find(BLOCK_END, i)
    if j < 0:
        raise RuntimeError("BLOCK_END tidak ditemukan")
    j_end = j + len(BLOCK_END)
    text = text[:i] + NEW_BLOCK + text[j_end:]

    p.write_text(text, encoding="utf-8", errors="surrogateescape")
    print(f"OK: {p} dipatch ({MARK}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
