// ui_overlay.h — Dear ImGui overlay for the unofficial port (original work).
#pragma once

#include <cstddef>
#include <string>

namespace ui {

struct OverlaySettings {
    int fps = 30;            // 0 = unlimited
    int touch_preset = 0;  // 0 = left thumb, 1 = right thumb, 2 = classic
    int sensitivity = 50;  // 0..100
    float res_scale = 1.0f;
    bool show_fps = false;
    bool vsync = true;
};

void overlay_init(const char* log_path, const char* ini_path);
void overlay_shutdown();
void overlay_new_frame(float dt);
void overlay_render();          // draws gear + panel, call inside ImGui frame
void overlay_toggle_gear();     // Back key: show/hide gear button
bool overlay_gear_visible();
bool overlay_panel_open();
bool overlay_quit_requested();
const OverlaySettings& overlay_settings();
void overlay_logf(const char* fmt, ...);  // also appended to recomp.log

}  // namespace ui
