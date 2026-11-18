#!/usr/bin/env python3
"""patch_gxm_render.py — fondasi render nyata: decode .bstex + GL texture quad.

Stub GXM tidak pernah menerima command buffer asli (guest belum panggil GXM),
jadi saya tidak bisa menerjemahkan command buffer. Yang BISA saya lakukan:
mengambil tekstur asli game (.bstex, format ARGB8888 / 0x0c, swizzled) dan
menampilkannya di layar lewat pipeline GL yang benar-benar ada: shader, VBO,
glBindTexture, glDrawArrays.

Ini BUKAN game render — ini "texture preview" yang membuktikan:
  * decoder aset jalan
  * GL pipeline jalan
  * layar tidak lagi hitam

Pemakaian: python3 tools/patch_gxm_render.py <app/src/main/cpp>
"""
from __future__ import annotations

import sys
from pathlib import Path

MARK = "[GXM_RENDER]"
ANCHOR_INC = "#include <GLES3/gl3.h>"
ANCHOR_SCAN = '            if      (ext == ".bstex")  { if (c.btex < MAX_TEXTURE_LOG)'
ANCHOR_CLEAR = (
    "        glClearColor(0.0f, 0.0f, 0.0f, 1.0f);\n"
    "        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);"
)

HELPERS = r'''
// [GXM_RENDER] Fondasi render nyata: decode .bstex ARGB8888 + GL quad.
namespace gxmview {
struct State {
    GLuint prog = 0, vbo = 0, tex = 0;
    bool tried = false, ready = false;
    std::string path;
    int w = 0, h = 0;
    int best_score = -1;
};
static State st;

static void logf(const char* tag, const char* fmt, ...) {
    char b[512]; va_list ap; va_start(ap, fmt);
    vsnprintf(b, sizeof(b), fmt, ap); va_end(ap);
    __android_log_print(ANDROID_LOG_WARN, tag, "%s", b);
    if (rc_dbg_log_hook) rc_dbg_log_hook(tag, b);
}

// Morton Z-order dalam tile 8x8 (layout swizzle PS Vita / GXT).
static inline uint32_t morton8(uint32_t x, uint32_t y) {
    return (x & 1u) | ((y & 1u) << 1) | ((x & 2u) << 1) | ((y & 2u) << 2) |
           ((x & 4u) << 2) | ((y & 4u) << 3);
}

// Decode GXT -> RGBA8888. Hanya format 0x0c (ARGB8888) yang didukung.
static bool decode_btex(const std::string& p, std::vector<uint8_t>& out,
                        int& ow, int& oh) {
    FILE* f = fopen(p.c_str(), "rb");
    if (!f) return false;
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    if (sz < 0x60) { fclose(f); return false; }
    std::vector<uint8_t> d((size_t)sz);
    if (fread(d.data(), 1, (size_t)sz, f) != (size_t)sz) { fclose(f); return false; }
    fclose(f);
    auto u32 = [&](size_t o) -> uint32_t {
        return (uint32_t)d[o] | ((uint32_t)d[o+1] << 8) |
               ((uint32_t)d[o+2] << 16) | ((uint32_t)d[o+3] << 24);
    };
    auto u16 = [&](size_t o) -> uint16_t {
        return (uint16_t)((uint32_t)d[o] | ((uint32_t)d[o+1] << 8));
    };
    if (d[0x20] != 'G' || d[0x21] != 'X' || d[0x22] != 'T') return false;
    const size_t info = u32(0x2c);
    if (info + 0x20 > d.size()) return false;
    const uint32_t fmt = u32(info + 0x14);
    if (fmt != 0x0c000000u) return false;  // ARGB8888 saja
    const uint32_t type = u32(info + 0x10);
    const int w = u16(info + 0x18), h = u16(info + 0x1a);
    if (w <= 0 || h <= 0 || w > 1024 || h > 1024) return false;
    const size_t data = info + 0x20;
    out.assign((size_t)w * (size_t)h * 4u, 0);
    const bool linear = (type == 0x60000000u) || (type == 0x0c000000u);
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            size_t src;
            if (linear) {
                src = data + (((size_t)y * (size_t)w + (size_t)x) * 4u);
            } else {
                const uint32_t bx = (uint32_t)x >> 3, by = (uint32_t)y >> 3;
                const uint32_t bw = ((uint32_t)w + 7u) >> 3;
                const size_t block = ((size_t)by * (size_t)bw + (size_t)bx) * 64u;
                src = data + (block + (size_t)morton8((uint32_t)x & 7u,
                                                       (uint32_t)y & 7u)) * 4u;
            }
            if (src + 4 > d.size()) continue;
            const size_t dst = ((size_t)y * (size_t)w + (size_t)x) * 4u;
            // GXT ARGB8888 (fmt 0x0c): layout memori [A,R,G,B] (big-endian word
            // 0xAARRGGBB). Tukar ke RGBA untuk glTexImage2D.
            out[dst + 0] = d[src + 1];  // R
            out[dst + 1] = d[src + 2];  // G
            out[dst + 2] = d[src + 3];  // B
            out[dst + 3] = d[src + 0];  // A
        }
    }
    ow = w; oh = h;
    return true;
}

static GLuint compile(GLenum kind, const char* src) {
    GLuint s = glCreateShader(kind);
    glShaderSource(s, 1, &src, nullptr);
    glCompileShader(s);
    GLint ok = 0; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { char b[512]; glGetShaderInfoLog(s, sizeof(b), nullptr, b);
               logf("GXM_RENDER", "shader fail: %s", b); glDeleteShader(s); return 0; }
    return s;
}

static bool init_gl() {
    static const char* vs =
        "attribute vec2 aPos; attribute vec2 aUV; varying vec2 vUV;\n"
        "void main(){ vUV=aUV; gl_Position=vec4(aPos,0.0,1.0); }\n";
    static const char* fs =
        "precision mediump float; varying vec2 vUV; uniform sampler2D uTex;\n"
        "void main(){ gl_FragColor=texture2D(uTex,vUV); }\n";
    GLuint v = compile(GL_VERTEX_SHADER, vs), f = compile(GL_FRAGMENT_SHADER, fs);
    if (!v || !f) return false;
    st.prog = glCreateProgram();
    glAttachShader(st.prog, v); glAttachShader(st.prog, f);
    glLinkProgram(st.prog);
    GLint ok = 0; glGetProgramiv(st.prog, GL_LINK_STATUS, &ok);
    if (!ok) return false;
    glDeleteShader(v); glDeleteShader(f);
    static const float quad[] = {
        -1.f,-1.f, 0.f,1.f,   1.f,-1.f, 1.f,1.f,  -1.f,1.f, 0.f,0.f,
        -1.f,1.f, 0.f,0.f,    1.f,-1.f, 1.f,1.f,   1.f,1.f, 1.f,0.f
    };
    glGenBuffers(1, &st.vbo);
    glBindBuffer(GL_ARRAY_BUFFER, st.vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(quad), quad, GL_STATIC_DRAW);
    glGenTextures(1, &st.tex);
    glBindTexture(GL_TEXTURE_2D, st.tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    return true;
}

static void draw() {
    if (st.tried) {
        if (!st.ready) return;
    } else {
        st.tried = true;
        logf("GXM_RENDER", "draw() first call: candidates=%d best_score=%d path='%s'",
             st.best_score >= 0 ? 1 : 0, st.best_score,
             st.path.empty() ? "(none)" : st.path.c_str());
        if (st.path.empty()) {
            logf("GXM_RENDER", "tidak ada .bstex ARGB8888 untuk ditampilkan");
            return;
        }
        std::vector<uint8_t> px;
        if (!decode_btex(st.path, px, st.w, st.h)) {
            logf("GXM_RENDER", "decode gagal: %s", st.path.c_str());
            return;
        }
        if (!init_gl()) return;
        glBindTexture(GL_TEXTURE_2D, st.tex);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, st.w, st.h, 0,
                     GL_RGBA, GL_UNSIGNED_BYTE, px.data());
        st.ready = true;
        logf("GXM_RENDER", "tampilkan tekstur asli %dx%d: %s (px0=%02x %02x %02x %02x)",
             st.w, st.h, st.path.c_str(),
             px[0], px[1], px[2], px[3]);
    }
    glUseProgram(st.prog);
    glBindBuffer(GL_ARRAY_BUFFER, st.vbo);
    GLint pos = glGetAttribLocation(st.prog, "aPos");
    GLint uv = glGetAttribLocation(st.prog, "aUV");
    GLint tex = glGetUniformLocation(st.prog, "uTex");
    glEnableVertexAttribArray(pos);
    glVertexAttribPointer(pos, 2, GL_FLOAT, GL_FALSE, 16, (void*)0);
    glEnableVertexAttribArray(uv);
    glVertexAttribPointer(uv, 2, GL_FLOAT, GL_FALSE, 16, (void*)8);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, st.tex);
    glUniform1i(tex, 0);
    glDrawArrays(GL_TRIANGLES, 0, 6);
    glDisableVertexAttribArray(pos);
    glDisableVertexAttribArray(uv);
}
}  // namespace gxmview
'''


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    if len(args) != 1:
        print("pemakaian: patch_gxm_render.py <app/src/main/cpp>", file=sys.stderr)
        return 2
    p = Path(args[0]) / "android_main.cpp"
    text = p.read_text(encoding="utf-8", errors="surrogateescape")
    if MARK in text:
        print("OK: sudah terpatch.")
        return 0

    # 1) helper setelah include GLES3 (helper pakai std::vector -> tambah include)
    if ANCHOR_INC not in text:
        raise RuntimeError("anchor include tidak ditemukan")
    inc_add = ANCHOR_INC + "\n#include <vector>"
    text = text.replace(ANCHOR_INC, inc_add + "\n" + HELPERS, 1)

    # 2) saat scan, simpan path .bstex ARGB8888 pertama (baca header 0x60)
    if ANCHOR_SCAN not in text:
        raise RuntimeError("anchor scan tidak ditemukan")
    capture = (
        ANCHOR_SCAN + "\n"
        "            {\n"
        "                if (ext == \".bstex\") {\n"
        "                    FILE* fb = fopen(path_cstr, \"rb\");\n"
        "                    if (fb) {\n"
        "                        unsigned char hd[0x80];\n"
        "                        size_t nr = fread(hd, 1, sizeof(hd), fb); fclose(fb);\n"
        "                        if (nr >= 0x60 && hd[0x20]=='G' && hd[0x21]=='X' && hd[0x22]=='T') {\n"
        "                            uint32_t io = (uint32_t)hd[0x2c] | ((uint32_t)hd[0x2d]<<8) |\n"
        "                                          ((uint32_t)hd[0x2e]<<16) | ((uint32_t)hd[0x2f]<<24);\n"
        "                            if (io + 0x20 <= nr && io + 0x28 <= nr) {\n"
        "                                uint32_t fm = (uint32_t)hd[io+0x14] | ((uint32_t)hd[io+0x15]<<8) |\n"
        "                                              ((uint32_t)hd[io+0x16]<<16) | ((uint32_t)hd[io+0x17]<<24);\n"
        "                                if (fm == 0x0c000000u) {\n"
        "                                    // Skor = alpha byte pertama (0..255). Pilih yang paling opak.\n"
        "                                    int sc = hd[io+0x20];\n"
        "                                    if (sc > gxmview::st.best_score) {\n"
        "                                        gxmview::st.best_score = sc;\n"
        "                                        gxmview::st.path = path_cstr;\n"
        "                                    }\n"
        "                                }\n"
        "                            }\n"
        "                        }\n"
        "                    }\n"
        "                }\n"
        "            }"
    )
    text = text.replace(ANCHOR_SCAN, capture, 1)

    # 3) render loop: gambar quad setelah clear
    if ANCHOR_CLEAR not in text:
        raise RuntimeError("anchor clear tidak ditemukan")
    text = text.replace(
        ANCHOR_CLEAR,
        ANCHOR_CLEAR + "\n\n        // [GXM_RENDER] Tampilkan tekstur asli game (fondasi GXM->GL).\n"
        "        gxmview::draw();",
        1,
    )

    p.write_text(text, encoding="utf-8", errors="surrogateescape")
    print(f"OK: {p} dipatch ({MARK}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
