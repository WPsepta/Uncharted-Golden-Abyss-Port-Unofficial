# STATUS — Uncharted: Golden Abyss Unofficial Port

> Catatan jujur untuk siapa pun yang ingin melanjutkan proyek ini.
> Update terakhir: commit `c81801e`.

## Ringkasan singkat

Repositori ini adalah **launcher + shell native + kumpulan build-time patcher**
untuk port Android dari *Uncharted: Golden Abyss* (PS Vita, PCSA00029).

**Game BELUM bisa dimainkan.** Guest (kode game hasil rekompilasi) masih macet
di fase inisialisasi dan tidak pernah mencapai render. Yang sudah berfungsi
adalah scaffold-nya: launcher, pemindai aset, UI, controller, dan overlay log.

Jangan mengira build FULL langsung menghasilkan game yang jalan — tidak.

## Yang sudah pasti berfungsi

- Launcher WebView (`uncharted.html`): Mulai / Pengaturan / Tentang / Keluar
- Tanpa highlight biru & teks tidak bisa disalin (CSS `user-select:none` +
  blokir `selectstart`/`contextmenu` + `setLongClickable(false)`)
- Navigasi controller: D-Pad/arrow = pindah pilihan (tombol kuning),
  A / DPAD_CENTER / Enter = jalankan, B = tutup panel
  (`MainActivity.dispatchKeyEvent()` → `window.gamepadKey()`)
- Pemindai aset (~28.336 file) + loader `.bstex` + overlay ImGui
- CI GitHub Actions menghasilkan APK dari `source_code.zip` yang diunggah manual

## Kondisi guest saat ini (dari log device)

```
hle_calls=0     ← guest TIDAK PERNAH memanggil fungsi GXM mana pun
```

Loop yang berulang:

```
0x81386c46 → 0x81389768 → 0x81389498 → ... → 0x813897a6 → ulang
r5 naik ~0x80000 (512 KiB) per iterasi
```

Fase terakhir yang diperbaiki: `sceKernelAllocMemBlock` dengan batas 256 MiB
dan heap cyclic (wrap + eviksi). Hasilnya belum terverifikasi menkonvergen.

## Koreksi diagnosis penting (jangan diulang)

Dulu saya mengira `0x0c20d060` adalah alamat entry thread di modul eksternal.
**Itu salah.** Dari tabel import ELF + `vitasdk/db/360/SceSysmem.yml` +
call site ABI + nama block guest (`"test"`, `"AudioStreamBuffer"`):

| Stub | NID | Fungsi |
|---|---|---|
| `0x81815ca4` | `0xB9D5EBDE` | `sceKernelAllocMemBlock(name, memType, size, opt)` |
| `0x81815c94` | `0xB8EF5818` | `sceKernelGetMemBlockBase(uid, void** base)` |
| `0x81815c84` | `0xA91E15EE` | `sceKernelFreeMemBlock(uid)` |

`0x0c20d060` / `0x0c208060` adalah konstanta
`SCE_KERNEL_MEMBLOCK_TYPE_USER_MAIN_RW` / `_NC_RW` — **bukan alamat kode**.

Guest melakukan binary-search ukuran blok: `size = r5 + 0x08000000`, lalu
`cmp r0,0; bge` menentukan cabang sukses/gagal.

## Sumber pengetahuan yang dipakai

- `https://vitasdk.org/` → API docs + repo `vitasdk/vita-headers`
- `vita-headers/db/360/*.yml` — 156 file, 9.464 NID. Dari 431 stub di project
  ini, **398 berhasil dinamai**, 33 masih anonim.
- Modul terbanyak: SceGxm (121), SceLibFios2 (42), SceLibc (39),
  SceAvcodecUser (28), SceCommonDialog (27).

Nama GXM yang sudah terpetakan (misalnya): `sceGxmInitialize` (`#77`),
`sceGxmCreateContext` (`#106`), `sceGxmBeginScene` (`#7`), `sceGxmDraw` (`#11`),
`sceGxmEndScene` (`#14`), `sceGxmDisplayQueueAddEntry` (`#109`).

## Yang masih hilang (pekerjaan sebenarnya)

1. **GXM HLE → OpenGL.** Ini bukan patch kecil. Perlu command buffer, shader,
   texture swizzling, render target, display queue. Tanpa ini tidak ada frame,
   termasuk menu game.
2. Scheduler / thread resmi (`sceKernelCreateThread` NID `0xC5C11EE7`,
   `sceKernelStartThread` `0xF08DE149` belum pernah terpanggil).
3. Input `sceCtrl`, font, dan sistem aset_runtime.

Rekomendasi: pelajari implementasi **Vita3K** sebagai referensi, lalu bidik
tonggak yang bisa diukur: *guest berhasil memanggil `sceGxmInitialize` /
`sceGxmCreateContext` lewat HLE*. Itu baru progres nyata berikutnya.

## Cara build

CI: jalankan `workflow_dispatch` pada `build_apk.yml` dengan input:

```
source_url = https://github.com/WPsepta/Uncharted-Golden-Abyss-Port-Unofficial/releases/download/V1/source_code.zip
```

Abaikan run `push` yang gagal — yang dipakai adalah `workflow_dispatch`
dengan `headSha` sama dengan commit terakhir.

Urutan patcher (di `tools/`), sesuai CI:

```
patch_hle_fallback.py → patch_gen_blocks.py → regen_invalid_region.py
→ patch_mrc_c13.py → patch_ldrd_strd.py → patch_spin_break.py
→ patch_bkpt.py → patch_gxm_render.py → patch_scan_async.py
```

Semua perbaikan kode game **wajib** berupa build-time patcher di `tools/`.

## Aturan clean-room (jangan dilanggar)

Jangan pernah commit ke repo publik:
- `gen/`, `recomp.cpp`, `recomp.h`
- dump / `.elf` / `.self` / `eboot.bin`
- `source_code.zip`
- aset game hasil dekripsi

Hak cipta game asli: Sony Interactive Entertainment / Bend Studio /
Naughty Dog. Proyek ini tidak berafiliasi dan tidak disetujui mereka.

## Catatan terakhir

Kalau tujuan Anda adalah **memainkan game sekarang**, Vita3K adalah jalan yang
sudah terbukti. Repo ini lebih cocok dipandang sebagai eksperimen
reverse-engineering jangka panjang.
