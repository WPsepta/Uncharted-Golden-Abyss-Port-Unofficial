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

#define LOG_TAG "uncharted"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

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
            while (!result.empty() && (result.back() == '\n' || result.back() == '\r')) result.pop_back();
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
    if (!fs::exists(root, ec) || !fs::is_directory(root, ec)) {
        c.errors = 1;
        return c;
    }
    fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec), end;
    if (ec) { c.errors = 1; return c; }
    for (; it != end; it.increment(ec)) {
        if (ec) { c.errors++; ec.clear(); continue; }
        const fs::directory_entry& e = *it;
        if (!e.is_regular_file()) continue;
        std::string ext = e.path().extension().string();
        for (auto& ch : ext) ch = static_cast<char>(tolower(static_cast<unsigned char>(ch)));
        c.total++;
        if      (ext == ".btex")  c.btex++;
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
    s->display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (s->display == EGL_NO_DISPLAY) return false;
    if (!eglInitialize(s->display, nullptr, nullptr)) return false;

    const EGLint cfg_attr[] = {
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
        EGL_BLUE_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_RED_SIZE, 8, EGL_ALPHA_SIZE, 8,
        EGL_DEPTH_SIZE, 16,
        EGL_NONE
    };
    EGLConfig cfg;
    EGLint n = 0;
    if (!eglChooseConfig(s->display, cfg_attr, &cfg, 1, &n) || n < 1) return false;

    EGLint fmt = 0;
    eglGetConfigAttrib(s->display, cfg, EGL_NATIVE_VISUAL_ID, &fmt);
    ANativeWindow_setBuffersGeometry(s->app->window, 0, 0, fmt);

    s->surface = eglCreateWindowSurface(s->display, cfg, s->app->window, nullptr);
    if (s->surface == EGL_NO_SURFACE) return false;
    const EGLint ctx_attr[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
    s->context = eglCreateContext(s->display, cfg, EGL_NO_CONTEXT, ctx_attr);
    if (s->context == EGL_NO_CONTEXT) return false;
    return eglMakeCurrent(s->display, s->surface, s->surface, s->context) == EGL_TRUE;
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
    ImGui_ImplOpenGL3_Init("#version 300 es");
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

    while (s->running.load()) {
        auto now = clock::now();
        float dt = std::chrono::duration<float>(now - last).count();
        last = now;

        // Frame limiter dari settings.ini (0 = unlimited)
        const int fps = ui::overlay_settings().fps;
        if (fps > 0) {
            const auto target = std::chrono::duration<float>(1.0f / fps);
            const auto elapsed = now - last;  // ~0, sisipkan tidur di bawah
            (void)elapsed;
            if (dt < target.count()) {
                std::this_thread::sleep_for(std::chrono::duration<float>(target.count() - dt));
            }
        }

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

        eglSwapBuffers(s->display, s->surface);

        if (ui::overlay_quit_requested()) {
            ui::overlay_logf("quit requested from overlay");
            ANativeActivity_finish(s->app->activity);
            s->running = false;
            break;
        }
    }
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
    ui::overlay_init(state.log_path, state.ini_path);
    ui::overlay_logf("=== Uncharted: Golden Abyss — Unofficial Port (scaffold) ===");
    ui::overlay_logf("Title ID: PCSA00029 | Content ID: UP9000-PCSA00029_00-0000000000000000");
    ui::overlay_logf("ini: %s", state.ini_path);

    const std::string asset_dir = read_ini_value(state.ini_path, "asset_dir");
    const std::string asset_uri = read_ini_value(state.ini_path, "asset_uri");
    ui::overlay_logf("asset_dir = %s", asset_dir.c_str());
    ui::overlay_logf("asset_uri = %s", asset_uri.c_str());
    if (asset_dir.empty()) {
        ui::overlay_logf("ERROR: asset_dir kosong — pilih folder aset di launcher, "
                         "pastikan folder dipetakan ke path file (mis. /storage/emulated/0/...), "
                         "lalu coba lagi.");
        ANativeActivity_finish(app->activity);
        return;
    } else {
        const std::string dir = asset_dir;
        std::thread([dir] {
            AssetCensus c = scan_assets(dir.c_str());
            // Reopen and append census (overlay logf appends too).
            ui::overlay_logf("asset census @ %s", dir.c_str());
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
            if (c.total == 0) {
                ui::overlay_logf("WARN: folder aset kosong atau tidak terbaca "
                                 "(izin? isi dump?).");
            }
        }).detach();
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
