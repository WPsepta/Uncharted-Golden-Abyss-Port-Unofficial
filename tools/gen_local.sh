#!/usr/bin/env bash
# tools/gen_local.sh — Siapkan "full build" dari dump MILIK SENDIRI.
#
# CATATAN HUKUM: skrip ini TIDAK mengunduh, mengekstrak, atau membagikan
# konten game. Anda harus sudah memiliki dump terdekripsi sendiri
# (mis. hasil FAGDec/NoNpDrm dari SELF milik Anda).
#
# Pemakaian:
#   UNCHARTED_RE=/root/uncharted_re tools/gen_local.sh
#   UNCHARTED_RE default: ../uncharted_re (sibling dari repo ini)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${UNCHARTED_RE:-$ROOT/../uncharted_re}"
DST="$ROOT/app/src/main/cpp"

if [ ! -d "$SRC" ]; then
    echo "error: $SRC tidak ditemukan. Set UNCHARTED_RE ke direktori hasil rekompilasi Anda." >&2
    exit 1
fi
if [ ! -f "$SRC/recomp.cpp" ] || [ ! -d "$SRC/gen" ]; then
    echo "error: $SRC harus berisi recomp.cpp dan gen/recompiled_partNNN.cpp" >&2
    exit 1
fi

# copy: recomp runtime + generated parts (ke folder yang di-gitignore)
cp "$SRC/recomp.cpp" "$SRC/recomp.h" "$DST/"
mkdir -p "$DST/gen"
cp "$SRC"/gen/recompiled_part[0-9][0-9][0-9].cpp "$DST/gen/"
cp "$SRC"/gen/recompiled_parts.cpp "$DST/gen/"

echo "OK: full-build sources disalin ke $DST/gen/"
echo "Jumlah part: $(ls "$DST"/gen/recompiled_part[0-9][0-9][0-9].cpp | wc -l)"
echo
echo "Build penuh lokal:"
echo "  gradle --no-daemon :app:assembleDebug"
echo "Hasil: app/build/outputs/apk/debug/app-debug.apk"
echo
echo "PERINGATAN: APK penuh berisi kode game hasil rekompilasi."
echo "JANGAN di-upload ke artifact/rilis/publik. Sideload untuk pemakaian pribadi saja."
