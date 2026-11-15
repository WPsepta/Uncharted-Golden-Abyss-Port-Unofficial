# Uncharted: Golden Abyss — Unofficial Port (Launcher + Native Shell)

Repositori ini adalah **scaffolding port tidak resmi** untuk Uncharted: Golden Abyss
(PS Vita, PCSA00029) ke Android. Isinya **hanya** karya asli:

- `MainActivity.java` — launcher: tombol **MULAI GAME**, pengaturan
  (Performance / Controls / System), **Baca Log Terakhir**, logo GitHub **WPsepta**.
- `android_main.cpp` + `ui_overlay.cpp` — NativeActivity + EGL/GLES3 + **Dear ImGui**:
  tombol **GEAR** di atas layar (bisa disembunyikan), panel ber-tab
  **[Performance] [Controls] [Cheat] [System] [Log] [Info]**.
- `CMakeLists.txt` — build NDK. Mode **STUB** (default, aman untuk publik) atau
  **FULL** (kode game Anda sendiri, tidak boleh dibagikan).
- `.github/workflows/build_apk.yml` — CI: JDK 17 + Android SDK/NDK + Gradle,
  build APK, upload artifact. **APK CI tidak berisi kode/aset game.**

## ⚠️ Legal — baca dulu

- **Tidak ada** kode game hasil dekripsi (`gen/recompiled_partNNN.cpp`, `recomp.cpp/h`)
  dan **tidak ada** aset game (tekstur, audio, model, dll.) di repositori ini.
- APK yang dihasilkan CI adalah **launcher/shell saja**; game tidak bisa dimainkan
  dari APK itu sampai Anda meng-generate kode game dari **dump milik Anda sendiri**.
- Untuk membangun build penuh: gunakan dump terdekripsi Anda sendiri (mis. hasil
  FAGDec/NoNpDrm), lalu:
  ```bash
  UNCHARTED_RE=/path/ke/hasil/rekompilasi tools/gen_local.sh
  gradle --no-daemon :app:assembleDebug
  ```
  Hasilnya sideload untuk **pemakaian pribadi**; jangan didistribusikan.
- Hak cipta game asli: Sony Interactive Entertainment / Bend Studio / Naughty Dog.
  Proyek ini tidak berafiliasi dan tidak disetujui oleh mereka.

## Struktur

```
app/src/main/java/com/uncharted/golden/abyss/unofficial/MainActivity.java
app/src/main/AndroidManifest.xml
app/src/main/res/{layout,drawable,values}/…
app/src/main/cpp/{CMakeLists.txt,android_main.cpp,ui_overlay.{cpp,h}}
.github/workflows/build_apk.yml
tools/gen_local.sh
```

## Build — dua mode

| Mode | Kapan | Isi APK |
|---|---|---|
| **STUB** (default, CI) | `cpp/gen/` kosong | Launcher + overlay; game tidak jalan |
| **FULL** | `tools/gen_local.sh` sudah dijalankan | Launcher + overlay + kode game Anda |

## Memuat game milik sendiri (workflow SAF)

1. Install APK (lokal: `gradle assembleDebug`; atau artifact CI sebagai launcher saja).
2. Di launcher, tekan **Pilih Folder Aset Game** dan arahkan ke folder dump aset
   Anda (mis. folder berisi `.bstex` / `.bsm` / `.xvag` hasil dump milik sendiri).
3. Launcher menyimpan pilihan itu (`settings.ini`), lalu tombol **MULAI GAME**
   aktif; engine (`android_main.cpp`) membaca `asset_dir` dari `settings.ini`
   dan melakukan indeks aset (count `.xvag/.bstex/.bsm/.bsanim/.hkx/...`) ke
   `recomp.log`.

## Launcher

- **MULAI GAME** — tulis `settings.ini` + buka NativeActivity.
- **Performance** — FPS 30/60/∞, resolution scale 50–100%, VSync, tampilkan FPS.
- **Controls** — preset touch (Thumb Left / Thumb Right / Classic), sensitivity 0–100.
- **Baca Log Terakhir** — menampilkan `recomp.log` dari app-private storage
  (58 KiB terakhir). Untuk trap `rc::`/fault guest, lihat `adb logcat -s recomp`.
- **Logo WPsepta** (kiri atas) → buka repo GitHub.

## Dalam game — Gear overlay

- **GEAR** (atas tengah): klik → panel [Performance] [Controls] [Cheat] [System]
  [Log] [Info].
- Tombol **Back** Android: tampilkan/sembunyikan Gear (jika panel terbuka → tutup panel).
- **System → KELUAR DARI GAME** untuk keluar ke launcher.
- **Cheat**: tidak ada — build ini sengaja tidak menyertakan cheat.

## Settings

`settings.ini` (app-private files dir, dibagi launcher & game):
`fps=30|60|0`, `touch_preset=0|1|2`, `sensitivity=0-100`, `show_fps=0|1`,
`vsync=0|1`, `res_scale=50-100`, `asset_dir=...`, `asset_uri=...`,
`use_game_elf=0|1`, `game_elf_path=/path/ke/uncharted_decrypted.elf`.

### Debug: boot engine dari ELF milik sendiri (`use_game_elf`)

**Default ON** (bisa dimatikan di **Pengaturan → "Boot game ELF (debug)"**).
Isi path ELF bila berbeda, lalu letakkan `uncharted_decrypted.elf` (hasil
dekripsi SELF milik Anda sendiri) di folder Download. Saat `use_game_elf=1`,
thread worker memanggil:

1. `rc::rc_boot()` — memetakan guest memory 256 MiB,
2. `rc::rc_load_elf_data(path)` — menyalin segmen `PT_LOAD` ke memori guest,
3. `rc::Cpu::run()` per-chunk 2 juta blok, dengan log progres
   `ENGINE_DEBUG: guest chunk ... blocks=... pc=...`.

Setiap import PS Vita yang belum dipetakan dicetak sebagai
`HLE_MISSING: Unmapped HLE import: ... (NID: 0x........)` lalu **return 0**
(tidak crash). Fault guest (SIGSEGV/SIGBUS/SIGILL/SIGFPE) di thread boot
dipulihkan lewat `siglongjmp` — aplikasi tetap hidup dan render loop terus
berjalan.

ELF game **tidak pernah** dibundel ke APK/repo; file itu tetap di perangkat
Anda. Tanpa ELF, flag ini hanya mencetak error dan boot dilewati.

## GitHub Actions

Workflow `build_apk.yml`:
1. `forbidden-files` — gagal bila ada file game (`recomp.cpp/h`, `gen/`, dump,
   `*.self`, `*.elf`, `eboot.bin`, `source_code.zip`) ikut ter-commit.
2. **Ambil `source_code.zip`** — kode game ( `recomp.cpp`, `recomp.h`,
   `gen/recompiled_part*.cpp` ) diunggah **manual** (GitHub web / release asset),
   tidak ikut git. CI mencari file itu di checkout; bila tidak ada, mengunduh
   dari `vars.SOURCE_ZIP_URL` / input `source_url`; bila tetap tidak ada, build
   gagal dengan pesan jelas. Isi zip diekstrak ke `app/src/main/cpp/`, lalu
   `tools/patch_hle_fallback.py` menambal `rc_hle_dispatch()` agar NID tak
   dikenal hanya log `HLE_MISSING` + `return 0` (tidak crash).
3. JDK 17 (Temurin) + Android SDK cmdline-tools + NDK 26.3 + Gradle 8.10.2,
   `gradle :app:assembleDebug`, upload artifact `Uncharted_Port-debug`.

## Lisensi

Karya asli scaffold ini: MIT (lihat LICENSE). Konten game asli: milik pemegang
hak ciptanya masing-masing dan tidak termasuk di sini.
