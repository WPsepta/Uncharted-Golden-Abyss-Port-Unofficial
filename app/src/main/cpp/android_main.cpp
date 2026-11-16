// android_main.cpp — NativeActivity shell for the unofficial port.
// Original work.
//   - EGL/GLES3 surface
//   - Dear ImGui + ui_overlay (Gear, Back = tampilkan/sembunyikan)
//   - Frame limiter from settings.ini (30/60/∞)
//   - Logs: <internal files>/recomp.log (launcher "Baca Log Terakhir" membaca ini)
//   - Clean room: tidak ada path aset hard-code. asset_dir/asset_uri dibaca dari
//     settings.ini (ditulis MainActivity) -> engine mencari .bstex/.bsm/.dll/...
//     di folder pilihan user, bukan di folder internal aplikasi.
//   - Full game build (gen/recompiled_part*.cpp hadir, gitignored): mem-boot
//     rc::Cpu di thread worker. Scaffold tetap tidak menyertakan konten game.

#include "ui_overlay.h"

#if RC_WITH_GAME_CODE
#include "recomp.h"

// Telemetri fault dari recomp.cpp. Didefinisikan oleh tools/patch_hle_fallback.py
// (blok [FAULT_DBG_GLOBALS]) dengan C linkage; deklarasinya harus sama persis.
extern "C" {
extern uint32_t rc_dbg_fault_addr;
extern uint32_t rc_dbg_fault_pc;
extern uint32_t rc_dbg_fault_write;
extern uint32_t rc_dbg_fault_seen;
extern uint32_t rc_dbg_unimpl_pc;
extern const char* rc_dbg_unimpl_what;
extern uint32_t rc_dbg_last_pc;
extern uint32_t rc_dbg_last_nid;
extern uint64_t rc_dbg_hle_calls;
extern uint32_t rc_dbg_missing_pc;
extern uint32_t rc_dbg_missing_from;
extern uint32_t rc_dbg_stub_missing;
extern uint64_t rc_dbg_stub_returns;
extern uint32_t rc_dbg_hist_pc[16];
extern uint32_t rc_dbg_hist_n;
extern uint32_t rc_dbg_hist_i;
extern uint64_t rc_dbg_step_total;
typedef void (*rc_dbg_log_fn)(const char* tag, const char* msg);
extern rc_dbg_log_fn rc_dbg_log_hook;
bool rc_dbg_has_block(uint32_t pc);
void rc_dbg_clear(void);
}
#endif

#include <android/native_activity.h>
#include <android_native_app_glue.h>
#include <android/input.h>
#include <android/keycodes.h>
#include <android/log.h>
#include <EGL/egl.h>
#include <GLES3/gl3.h>

#include "imgui.h"
#include "backends/imgui_impl_android.h"
#include "backends/imgui_impl_opengl3.h"

#include <atomic>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cstdarg>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <filesystem>
#include <string>
#include <set>
#include <thread>
#include <system_error>
#include <signal.h>
#include <setjmp.h>
#include <ucontext.h>
#include <unistd.h>

#define LOG_TAG "uncharted"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)
#define EGLLOG(...) __android_log_print(ANDROID_LOG_DEBUG, "EGL_DEBUG", __VA_ARGS__)
#define RENDERLOG(...) __android_log_print(ANDROID_LOG_DEBUG, "RENDER_DEBUG", __VA_ARGS__)

static const char* egl_err_str(EGLint e) {
    switch (e) {
        case EGL_SUCCESS: return "EGL_SUCCESS";
        case EGL_NOT_INITIALIZED: return "EGL_NOT_INITIALIZED";
        case EGL_BAD_DISPLAY: return "EGL_BAD_DISPLAY";
        case EGL_BAD_ATTRIBUTE: return "EGL_BAD_ATTRIBUTE";
        case EGL_BAD_PARAMETER: return "EGL_BAD_PARAMETER";
        case EGL_BAD_CONFIG: return "EGL_BAD_CONFIG";
        case EGL_BAD_CONTEXT: return "EGL_BAD_CONTEXT";
        case EGL_BAD_SURFACE: return "EGL_BAD_SURFACE";
        case EGL_BAD_MATCH: return "EGL_BAD_MATCH";
        case EGL_BAD_ACCESS: return "EGL_BAD_ACCESS";
        case EGL_BAD_ALLOC: return "EGL_BAD_ALLOC";
        case EGL_BAD_NATIVE_PIXMAP: return "EGL_BAD_NATIVE_PIXMAP";
        case EGL_BAD_NATIVE_WINDOW: return "EGL_BAD_NATIVE_WINDOW";
        case EGL_CONTEXT_LOST: return "EGL_CONTEXT_LOST";
        default: return "EGL_UNKNOWN";
    }
}

// Runtime toggle: Mali/Unisoc sering bermasalah dengan ES3.0.
static bool force_es2 = true;
// Global btex count from asset scan (for render loop texture check)
static uint64_t g_btex_count = 0;
// Toggle smoke test (red frame). Set true to verify EGL pipeline.
static bool run_smoke_test = true;

// Read one "key=value" line from settings.ini (last one wins).
static std::string read_ini_value(const char* path, const char* key) {
    FILE* f = fopen(path, "r");
    if (!f) return {};
    char line[1024];
    std::string result;
    const size_t klen = strlen(key);
    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, key, klen) == 0 && line[klen] == '=') {
            result = line + klen + 1;
            while (!result.empty() && (result.back() == '\n' || result.back() == '\r' || result.back() == ' ')) result.pop_back();
        }
    }
    fclose(f);
    return result;
}

// Read-only asset census: count files by extension under `root`.
// The engine never opens or redistributes game data here; this is only an
// index so the overlay/launcher can show that the dump is complete.
struct AssetCensus {
    uint64_t btex = 0, bsm = 0, bsanim = 0, xvag = 0, hkx = 0, bsp = 0, bnk = 0;
    uint64_t bsfnt = 0, png = 0, csv = 0, dll = 0, other = 0, total = 0, errors = 0;
};

static AssetCensus scan_assets(const char* root) {
    namespace fs = std::filesystem;
    AssetCensus c;
    std::error_code ec;
    __android_log_print(ANDROID_LOG_ERROR, "ASSET_DEBUG", "Mencoba scan folder: %s", root ? root : "(null)");
    ui::overlay_logf("ASSET_DEBUG: Mencoba scan folder: %s", root ? root : "(null)");
    if (!root || root[0] == '\0') {
        __android_log_print(ANDROID_LOG_ERROR, "ASSET_DEBUG", "ERROR: root path kosong!");
        ui::overlay_logf("ASSET_DEBUG: ERROR: root path kosong!");
        c.errors = 1;
        return c;
    }
    if (!fs::exists(root, ec)) {
        __android_log_print(ANDROID_LOG_ERROR, "ASSET_DEBUG", "ERROR: path tidak ada: %s (ec=%s)", root, ec.message().c_str());
        ui::overlay_logf("ASSET_DEBUG: ERROR: path tidak ada: %s (ec=%s)", root, ec.message().c_str());
        c.errors = 1;
        return c;
    }
    if (!fs::is_directory(root, ec)) {
        __android_log_print(ANDROID_LOG_ERROR, "ASSET_DEBUG", "ERROR: bukan directory: %s (ec=%s)", root, ec.message().c_str());
        ui::overlay_logf("ASSET_DEBUG: ERROR: bukan directory: %s (ec=%s)", root, ec.message().c_str());
        c.errors = 1;
        return c;
    }
    fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec), end;
    if (ec) {
        __android_log_print(ANDROID_LOG_ERROR, "ASSET_DEBUG", "ERROR: iterator gagal: %s", ec.message().c_str());
        ui::overlay_logf("ASSET_DEBUG: ERROR: iterator gagal: %s", ec.message().c_str());
        c.errors = 1;
        return c;
    }
    // Track unique extensions for debugging
    std::set<std::string> unique_exts;
    // Sementara: batasi print log per-file untuk 100 tekstur pertama (hindari spam + tekanan memori)
    constexpr uint64_t MAX_TEXTURE_LOG = 100;
    for (; it != end; it.increment(ec)) {
        if (ec) { c.errors++; ec.clear(); continue; }
        try {
            const fs::directory_entry& e = *it;
            // Pakai overload error_code: JANGAN lempar exception (penyebab crash sebelum render loop)
            if (!e.is_regular_file(ec)) { ec.clear(); continue; }
            const char* path_cstr = e.path().c_str();
            if (!path_cstr || path_cstr[0] == '\0') { c.errors++; continue; }
            std::string ext = e.path().extension().string();
            for (auto& ch : ext) ch = static_cast<char>(tolower(static_cast<unsigned char>(ch)));
            c.total++;
            // Jangan proses file tanpa ekstensi (mis. 'temp_cin') — langsung skip setelah dihitung
            if (ext.empty()) { c.other++; continue; }
            // SCANNER_DEBUG: HANYA cetak jika ekstensi .bstex atau .png
            if (ext == ".bstex" || ext == ".png") {
                if (c.btex + c.png < MAX_TEXTURE_LOG) {
                    __android_log_print(ANDROID_LOG_DEBUG, "SCANNER_DEBUG", "FILE GAMBAR DITEMUKAN: %s", path_cstr);
                    ui::overlay_logf("SCANNER_DEBUG: FILE GAMBAR DITEMUKAN: %s", path_cstr);
                }
            }
            unique_exts.insert(ext);
            if      (ext == ".bstex")  { if (c.btex < MAX_TEXTURE_LOG) { __android_log_print(ANDROID_LOG_DEBUG, "LOADER_DEBUG", "Berhasil memuat tekstur: %s", path_cstr); ui::overlay_logf("LOADER_DEBUG: Berhasil memuat tekstur: %s", path_cstr); } c.btex++; }
            else if (ext == ".bsm")   c.bsm++;
            else if (ext == ".bsanim" || ext == ".bssceneanim") c.bsanim++;
            else if (ext == ".xvag")  c.xvag++;
            else if (ext == ".hkx")   c.hkx++;
            else if (ext == ".bsp")   c.bsp++;
            else if (ext == ".bnk")   c.bnk++;
            else if (ext == ".bsfnt") c.bsfnt++;
            else if (ext == ".png")   { c.png++; }
            else if (ext == ".csv")   c.csv++;
            else if (ext == ".dll")   c.dll++;
            else                      { c.other++; if (c.other <= 20) __android_log_print(ANDROID_LOG_DEBUG, "ASSET_DEBUG", "Ext: %s -> %s", ext.c_str(), e.path().filename().c_str()); }
        } catch (const std::exception& ex) {
            // JANGAN pernah crash karena satu entry aneh — catat dan lanjut
            c.errors++;
            __android_log_print(ANDROID_LOG_ERROR, "ASSET_DEBUG", "ENTRY SKIP (exception): %s", ex.what());
            ui::overlay_logf("ASSET_DEBUG: ENTRY SKIP (exception): %s", ex.what());
        } catch (...) {
            c.errors++;
            __android_log_print(ANDROID_LOG_ERROR, "ASSET_DEBUG", "ENTRY SKIP (unknown exception)");
            ui::overlay_logf("ASSET_DEBUG: ENTRY SKIP (unknown exception)");
        }
    }
    __android_log_print(ANDROID_LOG_DEBUG, "LOADER_DEBUG", "Scan dan Load SELESAI. Bersiap masuk render loop...");
    ui::overlay_logf("LOADER_DEBUG: Scan dan Load SELESAI. Bersiap masuk render loop...");
    // Log unique extensions found
    __android_log_print(ANDROID_LOG_ERROR, "SCANNER_DEBUG", "=== EKSTENSI UNIK YANG DITEMUKAN ===");
    ui::overlay_logf("SCANNER_DEBUG: === EKSTENSI UNIK YANG DITEMUKAN ===");
    for (const auto& ext : unique_exts) {
        try {
            __android_log_print(ANDROID_LOG_ERROR, "SCANNER_EXT", "  Ext: '%s'", ext.c_str());
            ui::overlay_logf("SCANNER_EXT:   Ext: '%s'", ext.c_str());
        } catch (...) {
            c.errors++;
        }
    }
    __android_log_print(ANDROID_LOG_ERROR, "ASSET_DEBUG", "Scan selesai: total=%llu btex=%llu png=%llu errors=%llu",
                        (unsigned long long)c.total, (unsigned long long)c.btex,
                        (unsigned long long)c.png, (unsigned long long)c.errors);
    ui::overlay_logf("ASSET_DEBUG: Scan selesai: total=%llu btex=%llu png=%llu errors=%llu",
                     (unsigned long long)c.total, (unsigned long long)c.btex,
                     (unsigned long long)c.png, (unsigned long long)c.errors);
    __android_log_print(ANDROID_LOG_ERROR, "CRASH_DEBUG", "Selesai load tekstur, bersiap masuk render loop");
    ui::overlay_logf("CRASH_DEBUG: Selesai load tekstur, bersiap masuk render loop");
    return c;
}

namespace {

struct AppState {
    android_app* app = nullptr;
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLSurface surface = EGL_NO_SURFACE;
    EGLContext context = EGL_NO_CONTEXT;
    std::atomic<bool> running{false};
    std::thread worker;
    std::thread tick_thread;              // dedicated dummy/game-loop tick (idle setelah halt)
    char log_path[512] = "recomp.log";
    char ini_path[512] = "settings.ini";
    bool imgui_ready = false;
    int window_width = 0;
    int window_height = 0;
#if RC_WITH_GAME_CODE
    rc::Cpu cpu{};                 // guest CPU (di-boot hanya jika use_game_elf=1)
#endif
};

AppState* g_app = nullptr;

void join_paths(char* dst, size_t n, const char* base, const char* file) {
    if (base && base[0]) snprintf(dst, n, "%s/%s", base, file);
    else snprintf(dst, n, "%s", file);
}

bool egl_init(AppState* s) {
    EGLLOG("eglGetDisplay(EGL_DEFAULT_DISPLAY)");
    s->display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    EGLLOG("  -> display=%p (EGL_NO_DISPLAY=%p)", (void*)s->display, (void*)EGL_NO_DISPLAY);
    if (s->display == EGL_NO_DISPLAY) {
        EGLLOG("  FAIL: EGL_NO_DISPLAY, eglGetError=%s", egl_err_str(eglGetError()));
        return false;
    }

    EGLint major = 0, minor = 0;
    EGLLOG("eglInitialize(display, &major, &minor)");
    if (!eglInitialize(s->display, &major, &minor)) {
        EGLLOG("  FAIL: eglInitialize, eglGetError=%s", egl_err_str(eglGetError()));
        return false;
    }
    EGLLOG("  OK: EGL %d.%d", major, minor);

    EGLLOG("eglBindAPI(EGL_OPENGL_ES_API)");
    EGLBoolean bindRes = eglBindAPI(EGL_OPENGL_ES_API);
    EGLLOG("  -> %s, eglGetError=%s", bindRes == EGL_TRUE ? "EGL_TRUE" : "EGL_FALSE", egl_err_str(eglGetError()));
    if (bindRes != EGL_TRUE) return false;

    if (force_es2) {
        EGLLOG("Requesting OpenGL ES 2.0 context (Mali fallback mode)");
    } else {
        EGLLOG("Requesting OpenGL ES 3.0 context");
    }

    const EGLint cfg_attr[] = {
        EGL_RENDERABLE_TYPE, force_es2 ? EGL_OPENGL_ES2_BIT : EGL_OPENGL_ES3_BIT,
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
        EGL_BLUE_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_RED_SIZE, 8, EGL_ALPHA_SIZE, 8,
        EGL_DEPTH_SIZE, 16,
        EGL_NONE
    };
    EGLLOG("eglChooseConfig(display, cfg_attr, &cfg, 1, &n) RENDERABLE_TYPE=%s",
           force_es2 ? "EGL_OPENGL_ES2_BIT" : "EGL_OPENGL_ES3_BIT");
    EGLConfig cfg;
    EGLint n = 0;
    if (!eglChooseConfig(s->display, cfg_attr, &cfg, 1, &n) || n < 1) {
        EGLLOG("  FAIL: eglChooseConfig, n=%d, eglGetError=%s", n, egl_err_str(eglGetError()));
        return false;
    }
    EGLLOG("  OK: %d config(s) found", n);
    EGLint v = 0;
    eglGetConfigAttrib(s->display, cfg, EGL_RED_SIZE, &v); EGLLOG("  cfg RED_SIZE=%d", v);
    eglGetConfigAttrib(s->display, cfg, EGL_GREEN_SIZE, &v); EGLLOG("  cfg GREEN_SIZE=%d", v);
    eglGetConfigAttrib(s->display, cfg, EGL_BLUE_SIZE, &v); EGLLOG("  cfg BLUE_SIZE=%d", v);
    eglGetConfigAttrib(s->display, cfg, EGL_ALPHA_SIZE, &v); EGLLOG("  cfg ALPHA_SIZE=%d", v);
    eglGetConfigAttrib(s->display, cfg, EGL_DEPTH_SIZE, &v); EGLLOG("  cfg DEPTH_SIZE=%d", v);
    eglGetConfigAttrib(s->display, cfg, EGL_RENDERABLE_TYPE, &v); EGLLOG("  cfg RENDERABLE_TYPE=0x%x", v);

    EGLint fmt = 0;
    eglGetConfigAttrib(s->display, cfg, EGL_NATIVE_VISUAL_ID, &fmt);
    EGLLOG("ANativeWindow_setBuffersGeometry(window=%p, 0, 0, fmt=%d)", (void*)s->app->window, fmt);
    ANativeWindow_setBuffersGeometry(s->app->window, 0, 0, fmt);

    EGLLOG("eglCreateWindowSurface(display, cfg, window, nullptr) window=%p", (void*)s->app->window);
    s->surface = eglCreateWindowSurface(s->display, cfg, s->app->window, nullptr);
    EGLLOG("  -> surface=%p, eglGetError=%s", (void*)s->surface, egl_err_str(eglGetError()));
    if (s->surface == EGL_NO_SURFACE) return false;

    const EGLint ctx_attr[] = { EGL_CONTEXT_CLIENT_VERSION, force_es2 ? 2 : 3, EGL_NONE };
    EGLLOG("eglCreateContext(display, cfg, EGL_NO_CONTEXT, {CLIENT_VERSION=%d})", force_es2 ? 2 : 3);
    s->context = eglCreateContext(s->display, cfg, EGL_NO_CONTEXT, ctx_attr);
    EGLLOG("  -> context=%p, eglGetError=%s", (void*)s->context, egl_err_str(eglGetError()));
    if (s->context == EGL_NO_CONTEXT) return false;

    EGLLOG("eglMakeCurrent(display, surface, surface, context)");
    EGLBoolean mc = eglMakeCurrent(s->display, s->surface, s->surface, s->context);
    EGLLOG("  -> %s, eglGetError=%s", mc == EGL_TRUE ? "EGL_TRUE" : "EGL_FALSE", egl_err_str(eglGetError()));
    if (mc != EGL_TRUE) return false;

    const GLubyte* vendor = glGetString(GL_VENDOR);
    const GLubyte* renderer = glGetString(GL_RENDERER);
    const GLubyte* version = glGetString(GL_VERSION);
    EGLLOG("GL_VENDOR=%s", vendor ? (const char*)vendor : "(null)");
    EGLLOG("GL_RENDERER=%s", renderer ? (const char*)renderer : "(null)");
    EGLLOG("GL_VERSION=%s", version ? (const char*)version : "(null)");
    EGLLOG("glGetError() after glGetString = 0x%x", glGetError());

    return true;
}

void egl_shutdown(AppState* s) {
    if (s->display == EGL_NO_DISPLAY) return;
    if (s->imgui_ready) {
        ImGui_ImplOpenGL3_Shutdown();
        ImGui_ImplAndroid_Shutdown();
        ImGui::DestroyContext();
        s->imgui_ready = false;
    }
    eglMakeCurrent(s->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (s->context != EGL_NO_CONTEXT) eglDestroyContext(s->display, s->context);
    if (s->surface != EGL_NO_SURFACE) eglDestroySurface(s->display, s->surface);
    eglTerminate(s->display);
    s->display = EGL_NO_DISPLAY;
    s->surface = EGL_NO_SURFACE;
    s->context = EGL_NO_CONTEXT;
}

void imgui_init(AppState* s, ANativeWindow* window) {
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;  // jangan tulis imgui.ini
    ImGui::StyleColorsDark();
    ImGuiStyle& style = ImGui::GetStyle();
    style.WindowRounding = 14.0f;
    style.FrameRounding = 8.0f;
    style.GrabRounding = 8.0f;

    ImGui_ImplAndroid_Init(window);
    if (force_es2) {
        ImGui_ImplOpenGL3_Init("#version 100");
    } else {
        ImGui_ImplOpenGL3_Init("#version 300 es");
    }
    s->imgui_ready = true;
}

int32_t on_input(android_app* app, AInputEvent* ev) {
    AppState* s = static_cast<AppState*>(app->userData);
    if (!s) return 0;

    if (AInputEvent_getType(ev) == AINPUT_EVENT_TYPE_KEY) {
        const int32_t key = AKeyEvent_getKeyCode(ev);
        const int32_t action = AKeyEvent_getAction(ev);
        if (action == AKEY_EVENT_ACTION_DOWN) {
            if (key == AKEYCODE_BACK || key == AKEYCODE_MENU || key == AKEYCODE_ESCAPE) {
                ui::overlay_toggle_gear();
                ui::overlay_logf("input: %s -> gear %s",
                    key == AKEYCODE_BACK ? "BACK" : key == AKEYCODE_MENU ? "MENU" : "ESC",
                    ui::overlay_gear_visible() ? "visible" : "hidden");
                return 1;
            }
        }
        return 0;
    }

    if (AInputEvent_getType(ev) == AINPUT_EVENT_TYPE_MOTION) {
        if (s->imgui_ready) {
            return ImGui_ImplAndroid_HandleInputEvent(ev) ? 1 : 0;
        }
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Guest ELF boot (DEBUG, opt-in via settings.ini: use_game_elf=1)
// ---------------------------------------------------------------------------
// Recovery fault khusus thread worker yang sedang menjalankan guest.
// Thread lain (mis. render loop) tetap memakai jalur crash lama.
// Declared unconditionally: signal handler juga dikompilasi pada build stub.
static thread_local sigjmp_buf* t_guest_jmp = nullptr;
static thread_local volatile sig_atomic_t t_guest_armed = 0;

#if RC_WITH_GAME_CODE
// Path default; bisa dioverride di settings.ini: game_elf_path=/path/ke/elf
static const char* kDefaultElfPath =
    "/storage/emulated/0/Download/uncharted_decrypted.elf";

// Entry cadangan. e_entry di header ELF game ini (0x81005e35 -> 0x81005e34)
// jatuh 2 byte di tengah instruksi `bl`, sehingga blok hasil rekompilasi di
// sana tidak valid. module_start yang asli ada di 0x81009ffd, tetapi blok
// 0x81009ffc juga "invalid" di gen. Blok valid pertama yang bisa dieksekusi
// adalah crt0 di 0x8100a004 (target `bl` dari module_start).
// Bisa dioverride di settings.ini: guest_entry=0x8100a004 (0 = pakai e_entry).
static constexpr uint32_t kDefaultGuestEntry = 0x8100a004u;

// Maks blok guest per chunk. run() dipanggil per-chunk supaya kita bisa:
//   - mencetak progres (pc + jumlah blok),
//   - berhenti jika render thread/Activity dimatikan,
//   - pulih dari SIGSEGV guest tanpa membunuh aplikasi (siglongjmp).
static constexpr uint64_t kGuestBlocksPerChunk = 2000000ULL;

static void engine_logf(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    char buf[1024];
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    __android_log_print(ANDROID_LOG_DEBUG, "ENGINE_DEBUG", "%s", buf);
    ui::overlay_logf("ENGINE_DEBUG: %s", buf);
}

// Log fault guest: logcat tag GUEST_FAULT + baris "GUEST_FAULT: ..." di tab Log.
static void fault_logf(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    char buf[1024];
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    __android_log_print(ANDROID_LOG_ERROR, "GUEST_FAULT", "%s", buf);
    ui::overlay_logf("GUEST_FAULT: %s", buf);
}

static bool ini_truthy(const std::string& v) {
    return v == "1" || v == "true" || v == "TRUE" || v == "yes" || v == "on";
}

// Cetak state CPU + penyebab fault. Dipanggil tepat setelah run() berhenti
// karena faulted, atau setelah siglongjmp memulihkan sinyal dari guest.
// Tujuan: membedakan (a) instruksi belum diterjemahkan (RC_UNIMPL),
// (b) akses pointer/alamat memori tidak valid, (c) HLE import yang hilang.
static void log_guest_fault(AppState* s, const char* where) {
    rc::Cpu& c = s->cpu;
    const uint32_t pc = c.current_pc;

    fault_logf("[%s]: pc=0x%08x next=0x%08x thumb=%d blocks=%llu",
                where, pc, c.next_pc, (int)c.thumb,
                (unsigned long long)c.blocks_run);
    fault_logf("r0=0x%08x r1=0x%08x r2=0x%08x r3=0x%08x",
                c.r[0], c.r[1], c.r[2], c.r[3]);
    fault_logf("r4=0x%08x r5=0x%08x r6=0x%08x r7=0x%08x",
                c.r[4], c.r[5], c.r[6], c.r[7]);
    fault_logf("r8=0x%08x r9=0x%08x r10=0x%08x r11=0x%08x",
                c.r[8], c.r[9], c.r[10], c.r[11]);
    fault_logf("r12=0x%08x sp=0x%08x lr=0x%08x r15=0x%08x cpsr=0x%08x",
                c.r[12], c.r[13], c.r[14], c.r[15], c.cpsr);

    // Instruksi mentah di pc (blok yang sedang dieksekusi saat fault).
    uint8_t raw[8] = {0};
    if (c.in_range(pc, sizeof(raw))) {
        std::memcpy(raw, c.mem + (pc - rc::kMemBase), sizeof(raw));
        fault_logf("byte @pc 0x%08x = %02x %02x %02x %02x %02x %02x %02x %02x",
                    pc, raw[0], raw[1], raw[2], raw[3], raw[4], raw[5], raw[6], raw[7]);
    } else {
        fault_logf("byte @pc 0x%08x TIDAK BISA DIBACA "
                    "(pc di luar guest window).", pc);
    }

    if (rc_dbg_unimpl_pc != 0) {
        const char* what = rc_dbg_unimpl_what ? rc_dbg_unimpl_what : "?";
        if (rc_dbg_unimpl_pc != pc && c.in_range(rc_dbg_unimpl_pc, sizeof(raw))) {
            std::memcpy(raw, c.mem + (rc_dbg_unimpl_pc - rc::kMemBase), sizeof(raw));
            fault_logf("penyebab=RC_UNIMPL @0x%08x \"%s\" "
                        "byte=%02x %02x %02x %02x %02x %02x %02x %02x",
                        rc_dbg_unimpl_pc, what,
                        raw[0], raw[1], raw[2], raw[3], raw[4], raw[5], raw[6], raw[7]);
        } else {
            fault_logf("penyebab=RC_UNIMPL @0x%08x \"%s\"",
                        rc_dbg_unimpl_pc, what);
        }
        fault_logf("KESIMPULAN: BUKAN HLE, BUKAN pointer NULL — "
                    "translator tidak punya blok valid untuk instruksi ini.");
    } else if (rc_dbg_missing_pc != 0) {
        fault_logf("penyebab=NO_BLOCK target=0x%08x (dipanggil dari 0x%08x)",
                   rc_dbg_missing_pc, rc_dbg_missing_from);
        fault_logf("KESIMPULAN: translator tidak menghasilkan blok untuk alamat "
                   "ini (fungsi tidak terdeteksi / ARM-vs-Thumb salah). Ini BUKAN "
                   "HLE dan BUKAN pointer NULL.");
    } else if (rc_dbg_fault_seen) {
        const uint32_t a = rc_dbg_fault_addr;
        fault_logf("penyebab=memory %s addr=0x%08x (pc=0x%08x)%s",
                    rc_dbg_fault_write ? "WRITE" : "READ",
                    a, rc_dbg_fault_pc,
                    a == 0 ? " -> NULL POINTER!" : "");
        fault_logf("KESIMPULAN: pointer/alamat memori tidak valid "
                    "(bukan instruksi belum diterjemahkan).");
    } else {
        fault_logf("penyebab=tidak tercatat (kemungkinan sinyal CPU "
                    "native; lihat CPU_FAULT).");
    }

    if (rc_dbg_hle_calls > 0) {
        fault_logf("HLE terakhir: NID=0x%08x pemanggil_pc=0x%08x "
                    "total_panggilan=%llu",
                    rc_dbg_last_nid, rc_dbg_last_pc,
                    (unsigned long long)rc_dbg_hle_calls);
    } else {
        fault_logf("HLE belum pernah dipanggil — rc_hle_dispatch "
                    "tidak dieksekusi sama sekali.");
    }
}

// ---------------------------------------------------------------------------
// Dummy/game-loop tick — THREAD KHUSUS (anti ANR/busy-wait)
// ---------------------------------------------------------------------------
// Setelah module_start guest berhenti (ret_pc==0 / halt normal), game hanya
// bisa "bernapas" jika sesuatu memanggil fungsi game-loop (area 0x813833cc)
// dan stub grafis. Loop itu SENGAJA tidak berjalan di worker/main thread:
//   - tidur 16 ms + yield setiap tick (ANR guard),
//   - konteks CPU PENUH disimpan/di-restore mengelilingi SETIAP paksa
//     pemanggilan (r0/lr/cpsr/next_pc dll. TIDAK tercemar),
//   - thread TIDAK PERNAH berhenti selama s->running: kalau guest halt
//     (next_pc==0 / ret_pc==0), PC dipaksa balik ke game-loop entry
//     0x813833cc (area setelah 0x81383400).
// Recovery fault (SIGSEGV) memakai thread_local sigjmp_buf milik thread ini.
// 0x813833cc: prologue fungsi game loop; 0x81f00000: [TICK_TRAMPOLINE] —
// landing pad lr valid (bukan 0x00000000) untuk semua paksa pemanggilan.
// Trampoline diregistrasikan di fix-table oleh tools/patch_gen_blocks.py;
// bloknya menyetel next_pc=0 -> halt bersih -> tick thread lanjut tick berikut.
static constexpr uint32_t kDummyGameLoopEntry = 0x813833ccu;  // ARM prologue 0x813833cc
static constexpr uint32_t kTickTrampoline = 0x81f00000u;      // [TICK_TRAMPOLINE]

struct GuestCtxBackup {
    uint32_t r[16];
    uint64_t d[32];
    uint32_t cpsr;
    uint32_t fpscr;
    uint32_t next_pc;
    uint32_t current_pc;
    uint32_t thumb;
    bool running;
    bool faulted;
};

// thread_local: siglongjmp tidak mengubah storage thread_local static
static thread_local GuestCtxBackup t_ctx_backup{};

static void guest_ctx_save(rc::Cpu& c) {
    GuestCtxBackup& g = t_ctx_backup;
    for (int i = 0; i < 16; ++i) g.r[i] = c.r[i];
    for (int i = 0; i < 32; ++i) g.d[i] = c.d[i];
    g.cpsr = c.cpsr; g.fpscr = c.fpscr;
    g.next_pc = c.next_pc; g.current_pc = c.current_pc;
    g.thumb = c.thumb;
    g.running = c.running; g.faulted = c.faulted;
}

static void guest_ctx_restore(rc::Cpu& c) {
    const GuestCtxBackup& g = t_ctx_backup;
    for (int i = 0; i < 16; ++i) c.r[i] = g.r[i];
    for (int i = 0; i < 32; ++i) c.d[i] = g.d[i];
    c.cpsr = g.cpsr; c.fpscr = g.fpscr;
    c.next_pc = g.next_pc; c.current_pc = g.current_pc;
    c.thumb = g.thumb;
    c.running = g.running; c.faulted = g.faulted;
    // LR tidak boleh 0x00000000 pasca-restore: 0 = return ke null pointer.
    // Ganti dengan [TICK_TRAMPOLINE] supaya eksekusi guest berikutnya
    // mendarat di blok trampoline yang valid (halt bersih).
    if (c.r[14] == 0u) {
        static uint32_t rc_lr_subst_n = 0;
        if (rc_lr_subst_n < 3u || (rc_lr_subst_n % 1000u) == 0u) {
            engine_logf("guest_ctx_restore: lr==0 di-restore -> diganti "
                        "trampoline 0x%08x (bukan return ke null).", kTickTrampoline);
        }
        ++rc_lr_subst_n;
        c.r[14] = kTickTrampoline;
    }
}

static void dummy_tick_thread(AppState* s) {
    rc::Cpu& c = s->cpu;
    sigjmp_buf jb;
    t_guest_jmp = &jb;   // thread_local — khusus thread ini
    volatile uint64_t tick = 0;
    engine_logf("Dummy tick thread: mulai (tick 16 ms + save/restore ctx penuh).");
    while (s->running.load()) {
        guest_ctx_save(c);
        volatile bool halted = false;
        if (sigsetjmp(jb, 1) != 0) {
            // Pulih dari SIGSEGV guest di thread ini — JANGAN bunuh aplikasi.
            t_guest_armed = 0;
            __android_log_print(ANDROID_LOG_ERROR, "CPU_FAULT",
                "[TICK] Guest fault pulih di dummy tick thread: pc=0x%08x "
                "blocks=%llu", c.current_pc, (unsigned long long)c.blocks_run);
            ui::overlay_logf("CPU_FAULT: [TICK] Guest fault pulih (tick thread). "
                             "pc=0x%08x blocks=%llu",
                             c.current_pc, (unsigned long long)c.blocks_run);
            log_guest_fault(s, "tick-signal");
            halted = true;
        } else {
            t_guest_armed = 1;
            // Paksa eksekusi fungsi game-loop asli (ARM, entry 0x813833cc).
            // r0-r4 diisi pointer/args FAKE CONTEXT yang valid (0x81010000),
            // bukan 0 — supaya GXM_STUB/STUB_CALL tidak pernah mencetak
            // r0=0x00000000. lr = [TICK_TRAMPOLINE], BUKAN 0.
            for (int iter = 0; iter < 5 && s->running.load(); ++iter) {
                c.running = true;
                c.faulted = false;
                c.next_pc = kDummyGameLoopEntry;
                c.current_pc = kDummyGameLoopEntry;
                c.thumb = 0u;
                c.r[0] = 0x81010000u;   // fake context pointer (r0 valid)
                c.r[1] = 0x40u;
                c.r[2] = 0x81010000u;   // fake command buffer ptr
                c.r[3] = 0x40u;
                c.r[4] = 0x81010000u;   // arg utama (r0 = r4 di 0x813833fa)
                c.r[13] = 0x8EFFFF00u;  // SP aman
                c.r[14] = kTickTrampoline; // LR valid, bukan 0x00000000
                c.max_blocks = c.blocks_run + 20000ULL;
                c.run();
                if (c.faulted) {
                    engine_logf("Dummy tick: fault iter=%d pc=0x%08x next=0x%08x.",
                                iter, c.current_pc, c.next_pc);
                    c.faulted = false;
                    halted = true;
                    break;
                }
            }
            // Paksa panggil stub grafis setiap 5 tick, dengan arg valid + lr trampoline.
            if ((tick % 5u) == 0u) {
                const uint32_t stubs[2] = {0x81815464u, 0x81815494u};
                for (uint32_t addr : stubs) {
                    c.running = true;
                    c.faulted = false;
                    c.next_pc = addr;
                    c.current_pc = addr;
                    c.thumb = 0u;
                    c.r[0] = 0x81010000u;   // fake context
                    c.r[1] = 0x40u;
                    c.r[2] = 0x81010000u;   // fake command buffer ptr
                    c.r[3] = 0x40u;
                    c.r[13] = 0x8EFFFF00u;
                    c.r[14] = kTickTrampoline; // LR valid, bukan 0x00000000
                    c.max_blocks = c.blocks_run + 64ULL;
                    c.run();
                    if (c.faulted) c.faulted = false;
                }
            }
            t_guest_armed = 0;
            // Halts detection: ret_pc==0 / next_pc==0 / running==false
            if (c.next_pc == 0u || !c.running) halted = true;
        }
        // Restore konteks penuh — r0/lr/cpsr kembali seperti semula.
        guest_ctx_restore(c);
        if (halted) {
            // Jangan biarkan thread berhenti: paksa PC ke game-loop entry.
            c.running = true;
            c.faulted = false;
            c.next_pc = kDummyGameLoopEntry;
            c.current_pc = kDummyGameLoopEntry;
            c.thumb = 0u;
        }
        if ((tick % 10u) == 0u) {
            const uint32_t n = rc_dbg_hist_n > 16u ? 16u : rc_dbg_hist_n;
            engine_logf("Dummy tick heartbeat: tick=%llu blocks=%llu pc=0x%08x "
                        "lr=0x%08x r0=0x%08x steps=%llu hist=%u",
                        (unsigned long long)tick, (unsigned long long)c.blocks_run,
                        c.current_pc, c.r[14], c.r[0],
                        (unsigned long long)rc_dbg_step_total, n);
        }
        ++tick;
        std::this_thread::sleep_for(std::chrono::milliseconds(16));
        std::this_thread::yield();
    }
    t_guest_jmp = nullptr;
    t_guest_armed = 0;
    engine_logf("Dummy tick thread: berhenti (s->running=false).");
}

static void boot_guest_elf(AppState* s, const char* elf_path) {
    engine_logf("Memulai boot engine (rc_boot + rc_load_elf_data): %s", elf_path);

    errno = 0;
    FILE* f = fopen(elf_path, "rb");
    if (!f) {
        engine_logf("ELF TIDAK BISA DIBUKA: %s (errno=%d: %s). "
                    "Taruh uncharted_decrypted.elf di folder Download.",
                    elf_path, errno, strerror(errno));
        return;
    }
    unsigned char magic[4] = {0, 0, 0, 0};
    size_t nread = fread(magic, 1, sizeof(magic), f);
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    fclose(f);
    engine_logf("ELF ditemukan: %ld byte, magic=%02x%02x%02x%02x",
                fsize, magic[0], magic[1], magic[2], magic[3]);
    if (nread >= 4 && memcmp(magic, "SCE\0", 4) == 0) {
        engine_logf("INI FILE SELF VITA (magic SCE\\0), BUKAN ELF. SELF masih "
                    "terenkripsi/terbungkus dan tidak bisa dibaca rc_load_elf_data. "
                    "Gunakan ELF hasil dekripsi, mis. /storage/emulated/0/Download/"
                    "uncharted_decrypted.elf. Boot dibatalkan.");
        return;
    }
    if (nread < 4 || memcmp(magic, "\x7f" "ELF", 4) != 0) {
        engine_logf("BUKAN ELF yang valid (magic=%02x%02x%02x%02x). File ini "
                    "kemungkinan kontainer terenkripsi (SELF/PKG) atau hasil "
                    "ekstrak yang belum didekripsi. Boot dibatalkan.",
                    magic[0], magic[1], magic[2], magic[3]);
        return;
    }
    // Validasi tambahan: ELF Vita harus ELF32-LE ARM dengan entry di guest window.
    // Menolak lebih awal file yang header-nya ELF tapi segmennya masih terenkripsi.
    uint32_t header_entry = 0;
    {
        FILE* vf = fopen(elf_path, "rb");
        if (vf) {
            unsigned char eh[52] = {0};
            size_t got = fread(eh, 1, sizeof(eh), vf);
            fclose(vf);
            if (got >= sizeof(eh)) {
                uint32_t e_entry = 0, e_phoff = 0;
                memcpy(&e_entry, eh + 24, 4);
                memcpy(&e_phoff, eh + 28, 4);
                header_entry = e_entry;
                if (eh[4] != 1 || eh[5] != 1 || (e_entry < 0x81000000u) ||
                    e_phoff == 0 || e_phoff >= (uint32_t)fsize) {
                    engine_logf("Header ELF tidak cocok untuk guest Vita "
                                "(class=%u endian=%u entry=0x%08x phoff=0x%x). "
                                "Kemungkinan header saja yang terdekripsi. "
                                "Boot dibatalkan.", eh[4], eh[5], e_entry, e_phoff);
                    return;
                }
            }
        }
    }

    if (!rc::rc_boot(s->cpu)) {
        engine_logf("rc::rc_boot GAGAL (mmap guest memory 256 MiB ditolak). "
                    "Boot dibatalkan, aplikasi tetap berjalan.");
        return;
    }
    engine_logf("rc::rc_boot OK: guest memory 256 MiB terpetakan.");

    if (!rc::rc_load_elf_data(s->cpu, elf_path)) {
        engine_logf("rc_load_elf_data GAGAL: tidak ada PT_LOAD yang cocok "
                    "dengan guest window. Boot dibatalkan.");
        rc::rc_shutdown(s->cpu);
        return;
    }
    // --- Entry override -----------------------------------------------------
    // e_entry dari header tidak bisa dipakai (jatuh di tengah instruksi bl).
    // guest_entry=... di settings.ini memilih entry lain; 0 = pakai e_entry.
    uint32_t entry_pc = s->cpu.next_pc;
    uint32_t entry_thumb = s->cpu.thumb;
    const std::string ge = read_ini_value(s->ini_path, "guest_entry");
    uint32_t wanted = kDefaultGuestEntry;
    bool from_ini = false;
    if (!ge.empty()) {
        const char* begin = ge.c_str();
        char* end = nullptr;
        const unsigned long v = std::strtoul(begin, &end, 0);
        if (end && end != begin) { wanted = static_cast<uint32_t>(v); from_ini = true; }
        else engine_logf("guest_entry='%s' tidak valid (pakai default 0x%08x).",
                         ge.c_str(), kDefaultGuestEntry);
    }
    if (wanted == 0u) {
        engine_logf("guest_entry=0: memakai e_entry dari header ELF.");
    } else if (wanted < rc::kMemBase || wanted >= rc::kMemBase + rc::kMemSize) {
        engine_logf("guest_entry=0x%08x di luar guest window (0x%08x..0x%08x) — "
                    "diabaikan, pakai e_entry.", wanted, rc::kMemBase,
                    rc::kMemBase + rc::kMemSize);
    } else {
        entry_pc = wanted & ~1u;
        entry_thumb = wanted & 1u;
    }
    s->cpu.next_pc = entry_pc;
    s->cpu.current_pc = entry_pc;
    s->cpu.thumb = entry_thumb;
    s->cpu.cpsr = (s->cpu.cpsr & ~rc::kFlagT) | (entry_thumb ? rc::kFlagT : 0u);
    engine_logf("GUEST_ENTRY: header_elf=0x%08x module_start=0x81009ffd "
                "target_bl=0x8100a004 konfigurasi=%s efektif=0x%08x thumb=%u "
                "blok_ada=%d",
                header_entry, from_ini ? ge.c_str() : "(default)",
                entry_pc, entry_thumb,
                rc_dbg_has_block(entry_pc) ? 1 : 0);

    engine_logf("ELF data ter-load. Entry pc=0x%08x thumb=%d. Mulai eksekusi guest...",
                s->cpu.next_pc, (int)s->cpu.thumb);

    // Fungsi tanpa blok rekompilasi diperlakukan sebagai stub return -1 supaya
    // guest bisa melewati stub awal dan log menunjukkan hambatan sebenarnya.
    // Bisa dimatikan: settings.ini stub_missing_blocks=0 (batas 10000 return).
    const std::string sm = read_ini_value(s->ini_path, "stub_missing_blocks");
    rc_dbg_stub_missing = sm.empty() ? 1u : (ini_truthy(sm) ? 1u : 0u);
    engine_logf("stub_missing_blocks=%u%s — fungsi tanpa blok dianggap stub "
                "return -1 (batas 10000).",
                rc_dbg_stub_missing, sm.empty() ? " (default)" : "");

    // HLE_MISSING / RC_UNIMPL dari recomp.cpp juga masuk ke tab Log
    // (recomp.log), bukan hanya logcat.
    rc_dbg_log_hook = [](const char* tag, const char* msg) {
        __android_log_print(ANDROID_LOG_ERROR, tag, "%s", msg);
        ui::overlay_logf("%s: %s", tag, msg);
    };

    sigjmp_buf jb;
    t_guest_jmp = &jb;
    uint64_t chunks = 0;
    while (s->running.load()) {
        const uint64_t before = s->cpu.blocks_run;
        s->cpu.max_blocks = before + kGuestBlocksPerChunk;
        s->cpu.running = true;

        if (sigsetjmp(jb, 1) != 0) {
            // Kembali dari signal handler (guest fault) — JANGAN crash.
            t_guest_armed = 0;
            __android_log_print(ANDROID_LOG_ERROR, "CPU_FAULT",
                "Guest fault ter-recover: app TIDAK crash. pc=0x%08x blocks=%llu",
                s->cpu.current_pc, (unsigned long long)s->cpu.blocks_run);
            ui::overlay_logf("CPU_FAULT: Guest fault ter-recover (app TIDAK crash). "
                             "pc=0x%08x blocks=%llu",
                             s->cpu.current_pc, (unsigned long long)s->cpu.blocks_run);
            log_guest_fault(s, "signal");
            break;
        }
        rc_dbg_clear();
        t_guest_armed = 1;
        s->cpu.run();
        t_guest_armed = 0;

        ++chunks;
        engine_logf("guest chunk %llu: blocks=%llu pc=0x%08x running=%d faulted=%d",
                    (unsigned long long)chunks,
                    (unsigned long long)s->cpu.blocks_run,
                    s->cpu.current_pc, (int)s->cpu.running, (int)s->cpu.faulted);
        // ANR guard: chunk bisa panjang (kGuestBlocksPerChunk blok) — beri
        // jeda kecil + yield antar chunk supaya UI/Android watch dog tetap
        // mendapat CPU dan aplikasi tidak dianggap hang.
        std::this_thread::yield();
        std::this_thread::sleep_for(std::chrono::milliseconds(1));

        if (s->cpu.faulted) {
            log_guest_fault(s, "faulted");
            engine_logf("Guest berhenti: fault internal di pc=0x%08x — detail di "
                        "GUEST_FAULT di atas.", s->cpu.current_pc);
            break;
        }
        if (s->cpu.blocks_run == before) {
            log_guest_fault(s, "stall");
            engine_logf("Guest STALL: tidak ada blok rekompilasi di pc=0x%08x. "
                        "Eksekusi dihentikan (app tetap hidup).", s->cpu.next_pc);
            break;
        }
        const bool chunk_limit = (s->cpu.blocks_run >= s->cpu.max_blocks);
        if (!s->cpu.running && !chunk_limit) {
            engine_logf("Guest halt normal (running=false) di pc=0x%08x next=0x%08x. "
                        "Emulator tetap hidup (idle) — menunggu/tidak mematikan proses.",
                        s->cpu.current_pc, s->cpu.next_pc);
            // Trace: blok terakhir yang dieksekusi (ring buffer, urutan kronologis).
            {
                const uint32_t n = rc_dbg_hist_n > 16u ? 16u : rc_dbg_hist_n;
                char hbuf[512]; size_t hp = 0;
                hp += (size_t)snprintf(hbuf + hp, sizeof(hbuf) - hp,
                    "TRACE blok terakhir (%u, steps=%llu):", n,
                    (unsigned long long)rc_dbg_step_total);
                for (uint32_t k = 0; k < n && hp + 12 < sizeof(hbuf); ++k) {
                    const uint32_t idx = (rc_dbg_hist_i + 16u - n + k) & 15u;
                    hp += (size_t)snprintf(hbuf + hp, sizeof(hbuf) - hp, " 0x%08x", rc_dbg_hist_pc[idx]);
                }
                engine_logf("%s", hbuf);
                __android_log_print(ANDROID_LOG_DEBUG, "HALT_DEBUG", "%s", hbuf);
                if (rc_dbg_log_hook) rc_dbg_log_hook("HALT_DEBUG", hbuf);
            }
            // Jangan matikan emulator: idle loop agar UI tetap responsif.
            // Dummy/game-loop tick pindah ke THREAD KHUSUS (dummy_tick_thread):
            //   - tidur 16 ms + yield setiap tick (anti ANR/busy-wait),
            //   - konteks CPU disimpan/restore penuh tiap paksa pemanggilan
            //     (r0/lr/cpsr TIDAK jadi 0x00000000 di tick tinggi),
            //   - kalau ret_pc==0 / next_pc==0 / running==false, PC dipaksa
            //     balik ke game-loop entry 0x813833cc (thread tidak berhenti).
            // Worker menunggu tick thread di sini; tick keluar saat
            // s->running==false, SEBELUM rc::rc_shutdown() melepas guest memory.
            engine_logf("Dummy tick: memulai thread khusus (0x813833cc + stub grafis periodik, 16 ms/tick).");
            if (s->tick_thread.joinable()) s->tick_thread.join();
            s->tick_thread = std::thread(dummy_tick_thread, s);
            if (s->tick_thread.joinable()) s->tick_thread.join();
            engine_logf("Dummy tick thread selesai — lanjut rc_shutdown.");
            break;
        }
        // chunk_limit -> lanjut chunk berikutnya.
    }
    t_guest_jmp = nullptr;
    if (s->cpu.running || s->cpu.blocks_run > 0) {
        engine_logf("Guest loop selesai: total blocks=%llu. Melepas guest memory.",
                    (unsigned long long)s->cpu.blocks_run);
    } else {
        engine_logf("Boot loop selesai: total blocks=%llu. Melepas guest memory.",
                    (unsigned long long)s->cpu.blocks_run);
    }
    rc::rc_shutdown(s->cpu);
}
#endif  // RC_WITH_GAME_CODE

void on_cmd(android_app* app, int32_t cmd) {
    AppState* s = static_cast<AppState*>(app->userData);
    if (!s) return;
    switch (cmd) {
    case APP_CMD_INIT_WINDOW:
        if (app->window && egl_init(s)) {
            s->window_width = ANativeWindow_getWidth(app->window);
            s->window_height = ANativeWindow_getHeight(app->window);
            imgui_init(s, app->window);
            // SMOKE TEST: red frame to verify EGL/GL pipeline before render loop.
            glViewport(0, 0, s->window_width, s->window_height);
            glClearColor(1.0f, 0.0f, 0.0f, 1.0f);
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
            eglSwapBuffers(s->display, s->surface);
            EGLLOG("SMOKE TEST: red frame swapped, glGetError=0x%x", glGetError());
            ui::overlay_logf("SMOKE TEST: red frame swapped, glGetError=0x%x", glGetError());
            s->running = true;
            ui::overlay_logf("game window init: %dx%d", s->window_width, s->window_height);
#if RC_WITH_GAME_CODE
            s->worker = std::thread([s] {
                const std::string use_elf = read_ini_value(s->ini_path, "use_game_elf");
                std::string elf_path = read_ini_value(s->ini_path, "game_elf_path");
                if (elf_path.empty()) elf_path = kDefaultElfPath;
                const bool enabled = ini_truthy(use_elf);

                __android_log_print(ANDROID_LOG_DEBUG, "ENGINE_DEBUG",
                    "use_game_elf='%s' (enabled=%d) game_elf_path='%s'",
                    use_elf.c_str(), enabled ? 1 : 0, elf_path.c_str());
                ui::overlay_logf("ENGINE_DEBUG: use_game_elf='%s' (enabled=%d) game_elf_path='%s'",
                                 use_elf.c_str(), enabled ? 1 : 0, elf_path.c_str());

                if (!enabled) {
                    ui::overlay_logf("ENGINE_DEBUG: Boot engine DILEWATI. "
                                     "Aktifkan di Pengaturan launcher atau set "
                                     "use_game_elf=1 di settings.ini.");
                    return;
                }
                boot_guest_elf(s, elf_path.c_str());
            });
#else
            ui::overlay_logf("stub build: recompiled parts tidak disertakan (lihat README)");
#endif
        } else if (app->window) {
            ui::overlay_logf("FATAL: EGL init failed");
            ANativeActivity_finish(app->activity);
        }
        break;
    case APP_CMD_TERM_WINDOW:
        s->running = false;
        if (s->worker.joinable()) s->worker.join();
        if (s->tick_thread.joinable()) s->tick_thread.join();
        egl_shutdown(s);
        break;
    case APP_CMD_LOW_MEMORY:
        ui::overlay_logf("low memory");
        break;
    case APP_CMD_CONFIG_CHANGED:
        if (app->window) {
            s->window_width = ANativeWindow_getWidth(app->window);
            s->window_height = ANativeWindow_getHeight(app->window);
        }
        break;
    default:
        break;
    }
}

void render_loop(AppState* s) {
    using clock = std::chrono::steady_clock;
    auto last = clock::now();
    int frameCount = 0;
    bool firstFrame = true;

    __android_log_print(ANDROID_LOG_DEBUG, "RENDER_DEBUG", "=== MEMASUKI RENDER LOOP UTAMA ===");
    ui::overlay_logf("RENDER_DEBUG: === MEMASUKI RENDER LOOP UTAMA ===");
    while (s->running.load()) {
        auto now = clock::steady_clock::now();
        float dt = std::chrono::duration<float>(now - last).count();
        last = now;

        // CANARY LOG: hanya frame pertama (hindari spam ke tab Log).
        if (frameCount == 0) {
            __android_log_print(ANDROID_LOG_DEBUG, "RENDER_DEBUG", "Loop iteration: %d", frameCount);
            ui::overlay_logf("RENDER_DEBUG: Loop iteration: %d", frameCount);
        }

        // Log: Entry render loop pertama
        if (firstFrame) {
            __android_log_print(ANDROID_LOG_DEBUG, "RENDER_DEBUG", "Frame 1 started");
            ui::overlay_logf("RENDER_DEBUG: Frame 1 started");
            firstFrame = false;
        }

        // Frame limiter dari settings.ini (0 = unlimited)
        const int fps = ui::overlay_settings().fps;
        if (fps > 0) {
            const auto target = std::chrono::duration<float>(1.0f / fps);
            if (dt < target.count()) {
                std::this_thread::sleep_for(std::chrono::duration<float>(target.count() - dt));
            }
        }

        // Rendering debugging: shader compile status (query ringan, logcat saja)
        GLint prog = 0;
        glGetIntegerv(GL_CURRENT_PROGRAM, &prog);
        if (frameCount == 0) RENDERLOG("Render: GL_CURRENT_PROGRAM = %d", prog);

        // Rendering debugging: texture status (query bound texture, no creation)
        GLint activeTex = 0;
        glGetIntegerv(GL_ACTIVE_TEXTURE, &activeTex);
        // Query if a texture is currently bound - use 0 as default
        GLboolean hasTex = glIsTexture(0) ? GL_TRUE : GL_FALSE; // glIsTexture(0) returns GL_FALSE
        if (frameCount == 0) {
            RENDERLOG("Render: active texture unit = %x", activeTex);
            RENDERLOG("Render: glIsTexture(0) = %d (0 = tidak ada bound texture)", hasTex);
        }

        // Check if textures are available before drawing
        if (g_btex_count == 0 && frameCount == 0) {
            __android_log_print(ANDROID_LOG_WARN, "RENDER_DEBUG", "Skip draw: Tidak ada tekstur (btex=0 / .bstex=0)");
            ui::overlay_logf("RENDER_DEBUG: Skip draw: Tidak ada tekstur (btex=0 / .bstex=0)");
        }

        // Normal clear (no red flicker - smoke test moved to on_cmd, runs once)
        glClearColor(0.023f, 0.031f, 0.041f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);

        if (s->imgui_ready) {
            ImGuiIO& io = ImGui::GetIO();
            io.DisplaySize = ImVec2(static_cast<float>(s->window_width),
                                    static_cast<float>(s->window_height));
            ImGui_ImplOpenGL3_NewFrame();
            ImGui_ImplAndroid_NewFrame();
            ImGui::NewFrame();
            ui::overlay_new_frame(dt);
            ui::overlay_render();
            ImGui::Render();
            ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
        }

        // RENDER_DEBUG: TEPAT SETELAH draw call - query tekstur yang terikat.
        // Hanya frame pertama: log per-frame membuat tab Log penuh dan menutupi
        // ENGINE_DEBUG / HLE_MISSING / GUEST_FAULT yang justru ingin dibaca.
        {
            GLint current_texture_id = 0;
            glGetIntegerv(GL_TEXTURE_BINDING_2D, &current_texture_id);
            if (frameCount == 0) {
                __android_log_print(ANDROID_LOG_DEBUG, "RENDER_DEBUG", "Frame drawn with texture ID: %d", current_texture_id);
                ui::overlay_logf("RENDER_DEBUG: Frame drawn with texture ID: %d", current_texture_id);
            }
            // DRAW_DEBUG: draw utama di build ini adalah path ImGui (engine belum pernah memanggil GL)
            if (s->imgui_ready && frameCount == 0) {
                const int lists = ImGui::GetDrawData()->CmdListsCount;
                __android_log_print(ANDROID_LOG_DEBUG, "DRAW_DEBUG", "Engine meminta draw: CmdLists=%d, Texture ID: %d", lists, current_texture_id);
                ui::overlay_logf("DRAW_DEBUG: Engine meminta draw: CmdLists=%d, Texture ID: %d", lists, current_texture_id);
            }
        }
        // RENDER_DEBUG: glGetError TEPAT setelah draw call (error GPU Mali)
        {
            GLenum errDraw = glGetError();
            if (errDraw != GL_NO_ERROR) {
                __android_log_print(ANDROID_LOG_DEBUG, "RENDER_DEBUG", "GL error after draw: 0x%x", errDraw);
                ui::overlay_logf("RENDER_DEBUG: GL error after draw: 0x%x", errDraw);
            }
        }

        // Draw call counting (placeholder: ImGui draw calls)
        int drawCount = ImGui::GetDrawData()->CmdListsCount;
        if (frameCount == 0) RENDERLOG("Render: draw calls this frame = %d", drawCount);

        frameCount++;
        if (frameCount % 60 == 0) {
            RENDERLOG("Frame rendered: %d", frameCount);
        }

        eglSwapBuffers(s->display, s->surface);

        if (ui::overlay_quit_requested()) {
            ui::overlay_logf("quit requested from overlay");
            ANativeActivity_finish(s->app->activity);
            s->running = false;
            break;
        }
    }
}

// Global pointer for signal handler to access log path
static char g_crash_log_path[512] = "recomp.log";

// SIGSEGV handler - writes crash info to recomp.log
void sigsegv_handler(int sig, siginfo_t* info, void* ucontext) {
    // PRIORITAS 1: fault dari guest ELF (thread worker yang sedang rc::Cpu::run()).
    // Jangan bunuh aplikasi — log lalu siglongjmp balik ke boot loop.
    if (t_guest_armed && t_guest_jmp) {
        uintptr_t pc = 0;
#if defined(__aarch64__)
        if (ucontext) pc = (uintptr_t)((ucontext_t*)ucontext)->uc_mcontext.pc;
#elif defined(__arm__)
        if (ucontext) pc = (uintptr_t)((ucontext_t*)ucontext)->uc_mcontext.arm_pc;
#endif
        __android_log_print(ANDROID_LOG_ERROR, "CPU_FAULT",
            "Guest fault: sig=%d addr=%p pc=0x%lx — pemulihan via siglongjmp "
            "(aplikasi TIDAK crash)",
            sig, info ? info->si_addr : nullptr, (unsigned long)pc);
        t_guest_armed = 0;
        siglongjmp(*t_guest_jmp, 1);
    }

    FILE* f = fopen(g_crash_log_path, "a");
    if (f) {
        fprintf(f, "\n=== CRASH: signal %d ===\n", sig);
        fprintf(f, "Signal: %d\n", sig);
        if (info) {
            fprintf(f, "Fault address: %p\n", info->si_addr);
            fprintf(f, "Signal code: %d\n", info->si_code);
        }
        fprintf(f, "PID: %d, TID: %d\n", getpid(), gettid());
        fprintf(f, "=== END CRASH INFO ===\n");
        fflush(f);
        fclose(f);
    }
    // Also log to logcat
    __android_log_print(ANDROID_LOG_ERROR, "RENDER_DEBUG", "CRASH: signal %d at address %p, code=%d",
                        sig, info ? info->si_addr : nullptr, info ? info->si_code : 0);
    // Re-raise default handler to terminate
    signal(sig, SIG_DFL);
    raise(sig);
}

}  // namespace

void android_main(android_app* app) {
    AppState state;
    state.app = app;
    app->userData = &state;
    app->onAppCmd = on_cmd;
    app->onInputEvent = on_input;
    g_app = &state;

    const char* base = app->activity->internalDataPath;
    join_paths(state.log_path, sizeof(state.log_path), base, "recomp.log");
    join_paths(state.ini_path, sizeof(state.ini_path), base, "settings.ini");
    // Set crash log path for signal handler
    snprintf(g_crash_log_path, sizeof(g_crash_log_path), "%s", state.log_path);
    // Install crash/fault handlers. SIGSEGV/SIGBUS/SIGILL/SIGFPE: jika fault
    // berasal dari guest ELF, handler memulihkan (siglongjmp) alih-alih mematikan app.
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = sigsegv_handler;
    sa.sa_flags = SA_SIGINFO;
    sigaction(SIGSEGV, &sa, nullptr);
    sigaction(SIGBUS,  &sa, nullptr);
    sigaction(SIGILL,  &sa, nullptr);
    sigaction(SIGFPE,  &sa, nullptr);
    ui::overlay_init(state.log_path, state.ini_path);
    ui::overlay_logf("=== Uncharted: Golden Abyss — Port (scaffold) ===");
    ui::overlay_logf("Title ID: PCSA00029 | Content ID: UP9000-PCSA00029_00-0000000000000000");
    ui::overlay_logf("ini: %s", state.ini_path);

    const std::string asset_dir = read_ini_value(state.ini_path, "asset_dir");
    const std::string asset_uri = read_ini_value(state.ini_path, "asset_uri");
    ui::overlay_logf("asset_dir = %s", asset_dir.c_str());
    ui::overlay_logf("asset_uri = %s", asset_uri.c_str());
#if RC_WITH_GAME_CODE
    {
        std::string use_elf = read_ini_value(state.ini_path, "use_game_elf");
        std::string elf_path = read_ini_value(state.ini_path, "game_elf_path");
        if (elf_path.empty()) elf_path = kDefaultElfPath;
        ui::overlay_logf("use_game_elf = %s | game_elf_path = %s",
                         use_elf.empty() ? "(tidak diset -> boot dilewati)" : use_elf.c_str(),
                         elf_path.c_str());
        __android_log_print(ANDROID_LOG_DEBUG, "ENGINE_DEBUG",
                            "use_game_elf='%s' game_elf_path='%s'",
                            use_elf.c_str(), elf_path.c_str());
    }
#endif
    __android_log_print(ANDROID_LOG_ERROR, "ASSET_DEBUG", "Mencoba load asset_dir: %s", asset_dir.c_str());
    ui::overlay_logf("ASSET_DEBUG: Mencoba load asset_dir: %s", asset_dir.c_str());
    if (asset_dir.empty()) {
        ui::overlay_logf("ERROR: asset_dir kosong — pilih folder aset di launcher, "
                         "pastikan folder dipetakan ke path file (mis. /storage/emulated/0/...), "
                         "lalu coba lagi.");
        ANativeActivity_finish(app->activity);
        return;
    }

    // Scan rekursif (termasuk sub-folder seperti extracted/) — sinkron agar
    // keputusan render loop berdasarkan hasil nyata, bukan hang di loop hitam.
    const std::string dir = asset_dir;
    AssetCensus c = scan_assets(dir.c_str());   // recursive_directory_iterator
    g_btex_count = c.btex;
    ui::overlay_logf("asset census @ %s (recursive)", dir.c_str());
    ui::overlay_logf("  xvag=%llu  btex=%llu  bsm=%llu  bsanim=%llu  hkx=%llu",
                     (unsigned long long)c.xvag, (unsigned long long)c.btex,
                     (unsigned long long)c.bsm, (unsigned long long)c.bsanim,
                     (unsigned long long)c.hkx);
    ui::overlay_logf("  bsp=%llu  bnk=%llu  bsfnt=%llu  png=%llu  csv=%llu  dll=%llu  other=%llu",
                     (unsigned long long)c.bsp, (unsigned long long)c.bnk,
                     (unsigned long long)c.bsfnt, (unsigned long long)c.png,
                     (unsigned long long)c.csv, (unsigned long long)c.dll,
                     (unsigned long long)c.other);
    ui::overlay_logf("  total=%llu  errors=%llu",
                     (unsigned long long)c.total, (unsigned long long)c.errors);
    if (c.btex == 0 && c.bsm == 0) {
        ui::overlay_logf("ERROR: aset game tidak ditemukan (btex=0, bsm=0). "
                         "Cek path dan isi folder (termasuk sub-folder). "
                         "Kembali ke launcher.");
        ANativeActivity_finish(app->activity);
        return;
    }
#if RC_WITH_GAME_CODE
    ui::overlay_logf("build mode: FULL (gen/ parts ditemukan)");
#else
    ui::overlay_logf("build mode: STUB (gen/ parts tidak ada — lihat README)");
#endif

    while (!state.running.load()) {
        int events = 0;
        android_poll_source* source = nullptr;
        while (ALooper_pollOnce(1, nullptr, &events, reinterpret_cast<void**>(&source)) >= 0) {
            if (source) source->process(app, source);
            if (app->destroyRequested) return;
        }
    }

    // Semua event sampai window init (atau destroyRequested) sudah dipompa di
    // loop di atas; running=true hanya diset pada APP_CMD_INIT_WINDOW sukses.
    ui::overlay_logf("entering render loop");
    render_loop(&state);

    ANativeActivity_finish(app->activity);
    g_app = nullptr;
}
