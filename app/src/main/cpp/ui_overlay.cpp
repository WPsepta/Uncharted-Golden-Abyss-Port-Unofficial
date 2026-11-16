// ui_overlay.cpp — Dear ImGui in-game overlay for the unofficial port.
// Original work; no game code, no game data, no assets are embedded here.

#include "ui_overlay.h"

#include "imgui.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <mutex>
#include <string>
#include <vector>

namespace {

struct OverlayState {
    ui::OverlaySettings settings{};
    bool gear_visible = true;
    bool panel_open = false;
    float fps_ema = 60.0f;
    float dt = 1.0f / 60.0f;
    char ini_path[512] = "settings.ini";
    char log_path[512] = "recomp.log";
    char asset_dir[512] = "";
    char asset_uri[512] = "";
    std::string log_tail;
    bool quit_requested = false;
    int res_scale_pct = 100;
};

OverlayState g;

void load_ini() {
    FILE* f = fopen(g.ini_path, "r");
    if (!f) return;
    char line[256];
    while (fgets(line, sizeof(line), f)) {
        int fps = -1, preset = -1, sens = -1, show = -1, vsync = -1, res = -1;
        if (sscanf(line, "fps=%d", &fps) == 1 && fps >= 0) g.settings.fps = fps;
        if (sscanf(line, "touch_preset=%d", &preset) == 1 && preset >= 0 && preset <= 2) g.settings.touch_preset = preset;
        if (sscanf(line, "sensitivity=%d", &sens) == 1) g.settings.sensitivity = sens;
        if (sscanf(line, "show_fps=%d", &show) == 1) g.settings.show_fps = show != 0;
        if (sscanf(line, "vsync=%d", &vsync) == 1) g.settings.vsync = vsync != 0;
        if (sscanf(line, "res_scale=%d", &res) == 1 && res >= 50 && res <= 100) {
            g.res_scale_pct = res;
            g.settings.res_scale = res / 100.0f;
        }
        // asset_dir / asset_uri bisa mengandung spasi -> baca seluruh sisa baris.
        std::string val;
        auto match = [&](const char* key) -> bool {
            const size_t klen = strlen(key);
            if (strncmp(line, key, klen) == 0 && line[klen] == '=') {
                val.assign(line + klen + 1);
                while (!val.empty() && (val.back() == '\n' || val.back() == '\r')) val.pop_back();
                return true;
            }
            return false;
        };
        if (match("asset_dir")) snprintf(g.asset_dir, sizeof(g.asset_dir), "%s", val.c_str());
        if (match("asset_uri")) snprintf(g.asset_uri, sizeof(g.asset_uri), "%s", val.c_str());
    }
    fclose(f);
}

void save_ini() {
    FILE* f = fopen(g.ini_path, "w");
    if (!f) return;
    fprintf(f, "fps=%d\n", g.settings.fps);
    fprintf(f, "touch_preset=%d\n", g.settings.touch_preset);
    fprintf(f, "sensitivity=%d\n", g.settings.sensitivity);
    fprintf(f, "show_fps=%d\n", g.settings.show_fps ? 1 : 0);
    fprintf(f, "vsync=%d\n", g.settings.vsync ? 1 : 0);
    fprintf(f, "res_scale=%d\n", g.res_scale_pct);
    fprintf(f, "asset_dir=%s\n", g.asset_dir);
    fprintf(f, "asset_uri=%s\n", g.asset_uri);
    fclose(f);
}

void read_log_tail() {
    g.log_tail.clear();
    FILE* f = fopen(g.log_path, "rb");
    if (!f) {
        g.log_tail = "(recomp.log belum ada — jalankan game sekali)\n";
        return;
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    long start = size > 65536 ? size - 65536 : 0;
    fseek(f, start, SEEK_SET);
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) {
        g.log_tail.append(buf, n);
    }
    fclose(f);
    if (start > 0) {
        g.log_tail = "... (terpotong, " + std::to_string(size) + " byte total)\n\n" + g.log_tail;
    }
}

void clear_log() {
    FILE* f = fopen(g.log_path, "w");
    if (f) { fputs("", f); fclose(f); }
    g.log_tail.clear();
}

void gear_button() {
    ImGui::SetNextWindowPos(ImVec2(ImGui::GetIO().DisplaySize.x * 0.5f, 0.0f),
                            ImGuiCond_Always, ImVec2(0.5f, 0.0f));
    ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize
                           | ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing
                           | ImGuiWindowFlags_NoNav;
    ImGui::Begin("##gear", nullptr, flags);
    // Ikon gear (putih agak gelap) di tengah atas, bukan teks "GEAR".
    ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.18f, 0.20f, 0.24f, 0.75f));
    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.28f, 0.30f, 0.34f, 0.90f));
    ImGui::PushStyleColor(ImGuiCol_ButtonActive, ImVec4(0.35f, 0.37f, 0.41f, 1.00f));
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.85f, 0.85f, 0.88f, 1.00f));
    if (ImGui::Button("\xE2\x9A\x99", ImVec2(40.0f, 40.0f))) {
        g.panel_open = true;
    }
    ImGui::PopStyleColor(4);
    ImGui::SetItemTooltip("Pengaturan (Back = sembunyikan/tampilkan)");
    ImGui::End();
}

void tab_performance() {
    ImGui::TextUnformatted("Performa rendering");
    ImGui::Separator();
    ImGui::Text("FPS: %.0f (target %d)", g.fps_ema, g.settings.fps);
    ImGui::Spacing();
    int fps = g.settings.fps;
    if (ImGui::RadioButton("30 FPS", fps == 30)) { g.settings.fps = 30; save_ini(); }
    ImGui::SameLine();
    if (ImGui::RadioButton("60 FPS", fps == 60)) { g.settings.fps = 60; save_ini(); }
    ImGui::SameLine();
    if (ImGui::RadioButton("∞ (unlimited)", fps == 0)) { g.settings.fps = 0; save_ini(); }
    ImGui::Spacing();
    ImGui::Text("Resolution scale: %d%%", g.res_scale_pct);
    if (ImGui::SliderInt("##res", &g.res_scale_pct, 50, 100, "%d%%")) {
        g.settings.res_scale = g.res_scale_pct / 100.0f;
        save_ini();
    }
    if (ImGui::Checkbox("VSync", &g.settings.vsync)) save_ini();
    if (ImGui::Checkbox("Tampilkan FPS di layar", &g.settings.show_fps)) save_ini();
}

void tab_controls() {
    ImGui::TextUnformatted("Kontrol sentuh");
    ImGui::Separator();
    const char* presets[] = {"Thumb Left (default)", "Thumb Right", "Classic D-pad + Face"};
    int preset = g.settings.touch_preset;
    if (ImGui::Combo("Touch preset", &preset, presets, IM_ARRAYSIZE(presets))) {
        g.settings.touch_preset = preset;
        save_ini();
    }
    int sens = g.settings.sensitivity;
    if (ImGui::SliderInt("Sensitivity", &sens, 0, 100, "%d")) {
        g.settings.sensitivity = sens;
        save_ini();
    }
}

void tab_cheat() {
    ImGui::TextUnformatted("Cheat");
    ImGui::Separator();
    ImGui::TextWrapped("Tidak ada cheat tersedia di build ini.");
    ImGui::Spacing();
    ImGui::TextWrapped("Scaffolding port ini sengaja tidak menyertakan cheat code, kunci, "
                       "atau modifikasi game. Mengaktifkan cheat membutuhkan dukungan mesin "
                       "rekompilasi yang belum ada.");
}

void tab_system() {
    ImGui::TextUnformatted("Sistem");
    ImGui::Separator();
    ImGui::Text("Asset dir:");
    ImGui::PushTextWrapPos(ImGui::GetContentRegionAvail().x);
    ImGui::TextUnformatted(g.asset_dir[0] ? g.asset_dir : "(belum dipilih — tombol 'Pilih Folder Aset Game' di launcher)");
    if (g.asset_uri[0]) ImGui::TextUnformatted(g.asset_uri);
    ImGui::PopTextWrapPos();
    ImGui::Spacing();
    ImGui::Text("Log file:");
    ImGui::PushTextWrapPos(ImGui::GetContentRegionAvail().x);
    ImGui::TextUnformatted(g.log_path);
    ImGui::PopTextWrapPos();
    ImGui::Spacing();
    if (ImGui::Button("Bersihkan Log")) clear_log();
    ImGui::SameLine();
    if (ImGui::Button("Segarkan Log")) read_log_tail();
    ImGui::Spacing();
    if (ImGui::Button(g.gear_visible ? "Sembunyikan Gear" : "Tampilkan Gear")) {
        g.gear_visible = !g.gear_visible;
    }
    ImGui::Spacing();
    ImGui::TextWrapped("Catatan: trap rc:: dan fault memori guest di-log lewat logcat "
                       "(adb logcat -s recomp), bukan recomp.log.");
    ImGui::Spacing();
    ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.65f, 0.15f, 0.13f, 1.0f));
    if (ImGui::Button("KELUAR DARI GAME")) {
        g.quit_requested = true;
    }
    ImGui::PopStyleColor();
}

void tab_log() {
    ImGui::TextUnformatted("recomp.log (terakhir 64 KiB)");
    ImGui::SameLine();
    if (ImGui::SmallButton("Segarkan##logtab")) read_log_tail();
    ImGui::Separator();
    ImGui::BeginChild("##logscroll", ImVec2(0, 340), true, ImGuiWindowFlags_HorizontalScrollbar);
    ImGui::TextUnformatted(g.log_tail.c_str());
    ImGui::EndChild();
}

void tab_info() {
    ImGui::TextUnformatted("Uncharted: Golden Abyss — Port");
    ImGui::Separator();
    ImGui::BulletText("Title ID    : PCSA00029");
    ImGui::BulletText("Content ID  : UP9000-PCSA00029_00-0000000000000000");
    ImGui::BulletText("Application : com.uncharted.golden.abyss.unofficial");
    ImGui::BulletText("Repo        : github.com/WPsepta/Uncharted-Golden-Abyss-Port-Unofficial");
    ImGui::Spacing();
    ImGui::Separator();
    ImGui::Spacing();
    ImGui::TextWrapped("Legal: proyek ini adalah scaffolding port tidak resmi. "
                       "Kode game hasil dekripsi (gen/recompiled_partNNN.cpp) dan seluruh aset "
                       "game TIDAK disertakan di repositori maupun di APK CI. Untuk membangun "
                       "build penuh, Anda harus menggunakan dump milik Anda sendiri "
                       "(lihat README → 'Build penuh lokal').");
    ImGui::Spacing();
    ImGui::TextWrapped("Hak cipta game asli: Sony Interactive Entertainment / Bend Studio / Naughty Dog. "
                       "Proyek ini tidak berafiliasi dengan mereka.");
}

}  // namespace

namespace ui {

void overlay_init(const char* log_path, const char* ini_path) {
    if (log_path) { snprintf(g.log_path, sizeof(g.log_path), "%s", log_path); }
    if (ini_path) { snprintf(g.ini_path, sizeof(g.ini_path), "%s", ini_path); }
    load_ini();
    read_log_tail();
}

void overlay_shutdown() {}

void overlay_new_frame(float dt) {
    g.dt = dt;
    if (dt > 0.0f) {
        g.fps_ema = g.fps_ema * 0.90f + (1.0f / dt) * 0.10f;
    }
}

bool overlay_gear_visible() { return g.gear_visible; }
bool overlay_panel_open() { return g.panel_open; }
bool overlay_quit_requested() { return g.quit_requested; }
const OverlaySettings& overlay_settings() { return g.settings; }

void overlay_toggle_gear() {
    g.gear_visible = !g.gear_visible;
    if (!g.gear_visible) g.panel_open = false;
}

// Mutex untuk overlay_logf: dipanggil dari worker thread, dummy tick thread,
// DAN main/render thread. fopen/vfprintf/fflush dari thread berbeda tanpa
// lock bisa saling menimpa isi baris recomp.log (dan UB di beberapa libc).
std::mutex g_log_mtx;

void overlay_logf(const char* fmt, ...) {
    std::lock_guard<std::mutex> lk(g_log_mtx);
    va_list args;
    va_start(args, fmt);
    va_end(args);
    FILE* f = fopen(g.log_path, "a");
    if (f) {
        va_list args2;
        va_start(args2, fmt);
        vfprintf(f, fmt, args2);
        va_end(args2);
        fprintf(f, "\n");
        fflush(f);  // FLUSH: pastikan log tertulis sebelum close
        fclose(f);
    }
}

void overlay_render() {
    ImGuiIO& io = ImGui::GetIO();

    if (g.settings.show_fps) {
        ImGui::SetNextWindowPos(ImVec2(12.0f, 12.0f), ImGuiCond_Always);
        ImGui::SetNextWindowBgAlpha(0.55f);
        ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize
                               | ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing;
        ImGui::Begin("##fps", nullptr, flags);
        ImGui::Text("%.0f FPS", g.fps_ema);
        ImGui::End();
    }

    if (g.gear_visible && !g.panel_open) {
        gear_button();
    }

    if (g.panel_open) {
        ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x * 0.5f, 48.0f),
                                ImGuiCond_Always, ImVec2(0.5f, 0.0f));
        ImGui::SetNextWindowSize(ImVec2(520.0f, 480.0f), ImGuiCond_Always);
        ImGuiWindowFlags flags = ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoResize
                               | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings;
        if (ImGui::Begin("Pengaturan", &g.panel_open, flags)) {
            if (ImGui::BeginTabBar("panel_tabs")) {
                if (ImGui::BeginTabItem("Performance")) { tab_performance(); ImGui::EndTabItem(); }
                if (ImGui::BeginTabItem("Controls"))    { tab_controls();    ImGui::EndTabItem(); }
                if (ImGui::BeginTabItem("Cheat"))       { tab_cheat();       ImGui::EndTabItem(); }
                if (ImGui::BeginTabItem("System"))      { tab_system();      ImGui::EndTabItem(); }
                if (ImGui::BeginTabItem("Log"))         { tab_log();         ImGui::EndTabItem(); }
                if (ImGui::BeginTabItem("Info"))        { tab_info();        ImGui::EndTabItem(); }
                ImGui::EndTabBar();
            }
        }
        ImGui::End();
    }
}

}  // namespace ui
