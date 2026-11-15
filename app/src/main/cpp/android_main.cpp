// android_main.cpp — NativeActivity shell for the unofficial port.
// Original work.
//   - EGL/GLES3 surface
//   - Dear ImGui + ui_overlay (Gear, Back = tampilkan/sembunyikan)
//   - Frame limiter from settings.ini (30/60/∞)
//   - Logs: <internal files>/recomp.log (launcher "Baca Log Terakhir" membaca ini)
//   - Clean room: tidak ada path aset hard-code. asset_dir/asset_uri dibaca dari
//     settings.ini (ditulis MainActivity) -> engine mencari .btex/.bsm/.dll/...
//     di folder pilihan user, bukan di folder internal aplikasi.
//   - Full game build (gen/recompiled_part*.cpp hadir, gitignored): mem-boot
//     rc::Cpu di thread worker. Scaffold tetap tidak menyertakan konten game.

#include "ui_overlay.h"

#if RC_WITH_GAME_CODE
#include "recomp.h"
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
#include <chrono>
#include <cstring>
#include <filesystem>
#include <string>
#include <thread>
#include <system_error>
#include <signal.h>
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
    for (; it != end; it.increment(ec)) {
        if (ec) { c.errors++; ec.clear(); continue; }
        const fs::directory_entry& e = *it;
        if (!e.is_regular_file()) continue;
        std::string ext = e.path().extension().string();
        for (auto& ch : ext) ch = static_cast<char>(tolower(static_cast<unsigned char>(ch)));
        c.total++;
        if      (ext == ".btex")  { c.btex++; __android_log_print(ANDROID_LOG_DEBUG, "ASSET_DEBUG", "Found btex: %s", e.path().c_str()); ui::overlay_logf("ASSET_DEBUG: Found btex: %s", e.path().c_str()); }
        else if (ext == ".bsm")   c.bsm++;
        else if (ext == ".bsanim" || ext == ".bssceneanim") c.bsanim++;
        else if (ext == ".xvag")  c.xvag++;
        else if (ext == ".hkx")   c.hkx++;
        else if (ext == ".bsp")   c.bsp++;
        else if (ext == ".bnk")   c.bnk++;
        else if (ext == ".bsfnt") c.bsfnt++;
        else if (ext == ".png")   c.png++;
        else if (ext == ".csv")   c.csv++;
        else if (ext == ".dll")   c.dll++;
        else                      c.other++;
    }
    __android_log_print(ANDROID_LOG_ERROR, "ASSET_DEBUG", "Scan selesai: total=%llu btex=%llu png=%llu errors=%llu",
                        (unsigned long long)c.total, (unsigned long long)c.btex,
                        (unsigned long long)c.png, (unsigned long long)c.errors);
    ui::overlay_logf("ASSET_DEBUG: Scan selesai: total=%llu btex=%llu png=%llu errors=%llu",
                     (unsigned long long)c.total, (unsigned long long)c.btex,
                     (unsigned long long)c.png, (unsigned long long)c.errors);
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
    char log_path[512] = "recomp.log";
    char ini_path[512] = "settings.ini";
    bool imgui_ready = false;
    int window_width = 0;
    int window_height = 0;
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
                ui::overlay_logf("rc:: boot thread started (game code present)");
#if 0
                rc::rc_boot(s->cpu);  // not wired: HLE/loader still incomplete
#endif
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

    while (s->running.load()) {
        auto now = clock::steady_clock::now();
        float dt = std::chrono::duration<float>(now - last).count();
        last = now;

        // CANARY LOG: Check if render loop is running
        __android_log_print(ANDROID_LOG_DEBUG, "RENDER_DEBUG", "Loop iteration: %d", frameCount);
        ui::overlay_logf("RENDER_DEBUG: Loop iteration: %d", frameCount);

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

        // Rendering debugging: shader compile status (per-frame query, light)
        GLint prog = 0;
        glGetIntegerv(GL_CURRENT_PROGRAM, &prog);
        RENDERLOG("Render: GL_CURRENT_PROGRAM = %d", prog);

        // Rendering debugging: texture status (query bound texture, no creation)
        GLint activeTex = 0;
        glGetIntegerv(GL_ACTIVE_TEXTURE, &activeTex);
        RENDERLOG("Render: active texture unit = %x", activeTex);
        // Query if a texture is currently bound - use 0 as default
        GLboolean hasTex = glIsTexture(0) ? GL_TRUE : GL_FALSE; // glIsTexture(0) returns GL_FALSE
        RENDERLOG("Render: glIsTexture(0) = %d (0 = tidak ada bound texture)", hasTex);

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

        // GL error check after ImGui render
        GLenum err = glGetError();
        if (err != GL_NO_ERROR) {
            RENDERLOG("GL error after render: 0x%x", err);
        }

        // Draw call counting (placeholder: ImGui draw calls)
        int drawCount = ImGui::GetDrawData()->CmdListsCount;
        RENDERLOG("Render: draw calls this frame = %d", drawCount);

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
    FILE* f = fopen(g_crash_log_path, "a");
    if (f) {
        fprintf(f, "\n=== CRASH: SIGSEGV (Segmentation Fault) ===\n");
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
    __android_log_print(ANDROID_LOG_ERROR, "RENDER_DEBUG", "CRASH: SIGSEGV at address %p, code=%d",
                        info ? info->si_addr : nullptr, info ? info->si_code : 0);
    // Re-raise default handler to terminate
    signal(SIGSEGV, SIG_DFL);
    raise(SIGSEGV);
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
    // Install SIGSEGV handler
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = sigsegv_handler;
    sa.sa_flags = SA_SIGINFO;
    sigaction(SIGSEGV, &sa, nullptr);
    ui::overlay_init(state.log_path, state.ini_path);
    ui::overlay_logf("=== Uncharted: Golden Abyss — Port (scaffold) ===");
    ui::overlay_logf("Title ID: PCSA00029 | Content ID: UP9000-PCSA00029_00-0000000000000000");
    ui::overlay_logf("ini: %s", state.ini_path);

    const std::string asset_dir = read_ini_value(state.ini_path, "asset_dir");
    const std::string asset_uri = read_ini_value(state.ini_path, "asset_uri");
    ui::overlay_logf("asset_dir = %s", asset_dir.c_str());
    ui::overlay_logf("asset_uri = %s", asset_uri.c_str());
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
