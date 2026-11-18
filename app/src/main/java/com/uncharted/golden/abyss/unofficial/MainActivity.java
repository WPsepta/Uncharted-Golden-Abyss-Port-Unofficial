package com.uncharted.golden.abyss.unofficial;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.NativeActivity;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.DocumentsContract;
import android.provider.Settings;
import android.view.View;
import android.view.KeyEvent;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import android.webkit.WebView;
import android.webkit.JavascriptInterface;

import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {

    private static final String REPO_URL =
            "https://github.com/WPsepta/Uncharted-Golden-Abyss-Port-Unofficial.git";
    private static final int REQ_PICK_ASSETS = 4217;
    /** Default lokasi ELF terdekripsi (di luar APK; milik user sendiri). */
    private static final String DEFAULT_GAME_ELF_PATH =
            "/storage/emulated/0/Download/uncharted_decrypted.elf";

    private TextView tvAssetStatus;
    private Button btnStart;
    /** Cached folder-scan JSON pushed back to WebView. */
    private volatile String lastFolderStatusJson = "{}";

    private RadioGroup rgFps;
    private Spinner spTouchPreset;
    private SeekBar sbSensitivity;
    private Switch swShowFps;
    private Switch swVSync;

    // WebView for UI.html launcher
    private WebView webView;

    // Native Bridge interface
    public interface NativeBridge {
        void pickFolder();
        void startGame();
        void updatePath(String path);
        void showToast(String message);
        void exitGame();
        String readLog();
        void rescanFolder();
        String getFolderStatus();
    }
    private NativeBridge nativeBridge;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        ensureStoragePermission();

        // Initialize Native Bridge
        nativeBridge = new NativeBridge() {
            @Override
            public void pickFolder() {
                openFolderPicker();
            }

            @Override
            public void startGame() {
                if (!assetSelected()) {
                    Toast.makeText(MainActivity.this, "File game belum dipilih. Silakan pilih folder uncharted-extracted.", Toast.LENGTH_LONG).show();
                    openFolderPicker();
                    return;
                }
                writeSettings();
                Intent i = new Intent(MainActivity.this, NativeActivity.class);
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(i);
            }

            @Override
            public void updatePath(String path) {
                tvAssetStatus.setText("Folder aset: " + path);
            }

            @Override
            public void showToast(String message) {
                Toast.makeText(MainActivity.this, message, Toast.LENGTH_SHORT).show();
            }

            @Override
            public void exitGame() {
                finish();
            }

            @Override
            public String readLog() {
                File f = logFile();
                if (!f.exists()) return "recomp.log belum ada.\\nJalankan game minimal sekali.";
                try {
                    byte[] buf = new byte[(int) Math.min(f.length(), 64 * 1024)];
                    java.io.FileInputStream in = new java.io.FileInputStream(f);
                    int total = 0, n;
                    while (total < buf.length && (n = in.read(buf, total, buf.length - total)) > 0) {
                        total += n;
                    }
                    in.close();
                    return new String(buf, 0, total, StandardCharsets.UTF_8);
                } catch (java.io.IOException e) {
                    return "Gagal membaca log: " + e.getMessage();
                }
            }

            @Override
            public void rescanFolder() {
                startFolderScan(assetDirForIni());
            }

            @Override
            public String getFolderStatus() {
                return lastFolderStatusJson;
            }
        };

        tvAssetStatus = findViewById(R.id.tvAssetStatus);
        btnStart = findViewById(R.id.btnStart);

        // Initialize WebView with UI.html
        webView = findViewById(R.id.webView);
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setAllowFileAccess(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setLoadWithOverviewMode(true);
        webView.getSettings().setUseWideViewPort(true);
        webView.setFocusable(true);
        webView.setFocusableInTouchMode(true);
        webView.setClickable(true);
        // [NO_SELECT] Cegah tahan-lama memunculkan selection handle / copy popup.
        webView.setLongClickable(false);
        webView.setOnLongClickListener(v -> true);
        webView.addJavascriptInterface(new JavascriptBridge(nativeBridge), "NativeBridge");
        String htmlPath = "file:///android_asset/uncharted.html";
        webView.loadUrl(htmlPath);

        findViewById(R.id.btnPickAssets).setOnClickListener(v -> openFolderPicker());
        btnStart.setOnClickListener(v -> {
            if (!assetSelected()) {
                openFolderPicker();
                return;
            }
            writeSettings();
            Intent i = new Intent(this, NativeActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        });
        findViewById(R.id.btnSettings).setOnClickListener(v -> showSettingsDialog());
        findViewById(R.id.btnAbout).setOnClickListener(v -> showAboutDialog());
        findViewById(R.id.btnExit).setOnClickListener(v -> finish());

        rgFps = new RadioGroup(this);
        // placeholders; real views live in dialog

        readSettings();
        updateStartGate();
    }

    private void ensureStoragePermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (!Environment.isExternalStorageManager()) {
                try {
                    startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
                } catch (Exception e) {
                    android.util.Log.e("Launcher", "cannot open storage settings", e);
                }
            }
        }
    }

    /* [GAMEPAD] Teruskan tombol controller/keyboard ke menu WebView.
       D-Pad atas/bawah = pindah pilihan (tombol kuning), A/DPAD_CENTER = jalankan,
       B/BACK = tutup panel. Back sengaja diteruskan ke super agar navigation normal. */
    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (event.getAction() == KeyEvent.ACTION_DOWN && webView != null) {
            String code = null;
            switch (event.getKeyCode()) {
                case KeyEvent.KEYCODE_DPAD_UP:
                case KeyEvent.KEYCODE_DPAD_LEFT:
                    code = "UP"; break;
                case KeyEvent.KEYCODE_DPAD_DOWN:
                case KeyEvent.KEYCODE_DPAD_RIGHT:
                    code = "DOWN"; break;
                case KeyEvent.KEYCODE_BUTTON_A:
                case KeyEvent.KEYCODE_DPAD_CENTER:
                case KeyEvent.KEYCODE_ENTER:
                    code = "A"; break;
                case KeyEvent.KEYCODE_BUTTON_B:
                    code = "B"; break;
                default: break;
            }
            if (code != null) {
                final String js = "window.gamepadKey && window.gamepadKey('" + code + "')";
                webView.evaluateJavascript(js, null);
                if (!"B".equals(code)) return true;
            }
        }
        return super.dispatchKeyEvent(event);
    }

    // ------------------------------------------------------------------
    // Asset folder picker
    // ------------------------------------------------------------------
    private void openFolderPicker() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                 | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        startActivityForResult(i, REQ_PICK_ASSETS);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_PICK_ASSETS || resultCode != RESULT_OK || data == null) return;
        Uri uri = data.getData();
        if (uri == null) return;

        try {
            getContentResolver().takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (SecurityException e) {
            android.util.Log.e("Launcher", "persistable uri permission denied", e);
        }

        String path = derivePrimaryPath(uri);
        if (path == null || path.trim().isEmpty()) {
            Toast.makeText(this, "Folder tidak bisa dipetakan ke path file. Pilih folder di storage internal.", Toast.LENGTH_LONG).show();
            return;
        }

        android.content.SharedPreferences p = getSharedPreferences("launcher", MODE_PRIVATE);
        p.edit().putString("asset_uri", uri.toString()).apply();
        p.edit().putString("asset_dir", path).apply();

        tvAssetStatus.setText("Folder aset: " + path);
        writeSettings();
        updateStartGate();
        startFolderScan(path);
    }

    private static String derivePrimaryPath(Uri uri) {
        String docId = null;
        try {
            docId = DocumentsContract.getTreeDocumentId(uri);
        } catch (Exception e) {
            try {
                docId = DocumentsContract.getDocumentId(uri);
            } catch (Exception e2) {
                return null;
            }
        }
        if (docId == null) return null;
        int i = docId.indexOf(':');
        if (i < 0) return null;
        String vol = docId.substring(0, i);
        String rel = docId.substring(i + 1);
        if (rel.isEmpty()) return null;
        if ("primary".equalsIgnoreCase(vol)) return "/storage/emulated/0/" + rel;
        return "/storage/" + vol + "/" + rel;
    }

    private boolean assetSelected() {
        android.content.SharedPreferences p = getSharedPreferences("launcher", MODE_PRIVATE);
        String dir = p.getString("asset_dir", "");
        return dir != null && !dir.trim().isEmpty();
    }

    private String assetDirForIni() {
        android.content.SharedPreferences p = getSharedPreferences("launcher", MODE_PRIVATE);
        String dir = p.getString("asset_dir", "");
        return dir != null ? dir : "";
    }

    private String assetUriForIni() {
        android.content.SharedPreferences p = getSharedPreferences("launcher", MODE_PRIVATE);
        String uri = p.getString("asset_uri", "");
        return uri != null ? uri : "";
    }

    private void updateStartGate() {
        boolean ok = assetSelected();
        btnStart.setEnabled(ok);
        btnStart.setAlpha(ok ? 1.0f : 0.4f);
        if (ok) {
            String dir = assetDirForIni();
            tvAssetStatus.setText("Folder aset: " + (dir.isEmpty() ? assetUriForIni() : dir));
        }
    }

    // ------------------------------------------------------------------
    // Settings
    // ------------------------------------------------------------------
    private File settingsFile() {
        return new File(getFilesDir(), "settings.ini");
    }

    private File logFile() {
        return new File(getFilesDir(), "recomp.log");
    }

    private void readSettings() {
        android.content.SharedPreferences p =
                getSharedPreferences("launcher", MODE_PRIVATE);
        // keep defaults
    }

    /** Walk a directory recursively to count files by extension. */
    private static void walk(File dir, long[] c, int depth) {
        if (depth > 12) return;  // safety guard
        File[] fs = dir.listFiles();
        if (fs == null) return;
        for (File f : fs) {
            if (f.isDirectory()) {
                walk(f, c, depth + 1);
            } else if (f.isFile()) {
                c[0]++;  // total
                String name = f.getName().toLowerCase();
                int dot = name.lastIndexOf('.');
                String ext = (dot >= 0) ? name.substring(dot) : "";
                switch (ext) {
                    case ".bstex": c[1]++; break;
                    case ".bsm": c[2]++; break;
                    case ".bsanim": case ".bssceneanim": c[3]++; break;
                    case ".xvag": c[4]++; break;
                    case ".hkx": c[5]++; break;
                    case ".bsp": c[6]++; break;
                    case ".bnk": c[7]++; break;
                    case ".bsfnt": c[8]++; break;
                    case ".png": c[9]++; break;
                    case ".csv": c[10]++; break;
                    case ".dll": c[11]++; break;
                    default: c[12]++; break;
                }
            }
        }
    }

    /** Run folder scan in background; result cached & pushed to WebView. */
    private void startFolderScan(final String path) {
        if (path == null || path.trim().isEmpty()) return;
        new Thread(() -> {
            long[] c = new long[13];
            try {
                walk(new File(path), c, 0);
            } catch (Throwable ignored) {
            }
            String json = buildFolderStatusJson(path, c);
            lastFolderStatusJson = json;
            runOnUiThread(() -> {
                if (webView != null) {
                    webView.evaluateJavascript(
                        "window.setFolderStatus && window.setFolderStatus(" + json + ");", null);
                }
            });
        }, "folder-scan").start();
    }

    private String buildFolderStatusJson(String path, long[] c) {
        String folderName = path;
        try {
            folderName = new File(path).getName();
            if (folderName.isEmpty()) folderName = path;
        } catch (Throwable ignored) { }
        StringBuilder sb = new StringBuilder();
        sb.append('{')
          .append("\"folder\":\"").append(escapeJson(folderName)).append("\",")
          .append("\"total\":").append(c[0]).append(',')
          .append("\"btex\":").append(c[1]).append(',')
          .append("\"bsm\":").append(c[2]).append(',')
          .append("\"bsanim\":").append(c[3]).append(',')
          .append("\"xvag\":").append(c[4]).append(',')
          .append("\"hkx\":").append(c[5]).append(',')
          .append("\"bsp\":").append(c[6]).append(',')
          .append("\"bnk\":").append(c[7]).append(',')
          .append("\"bsfnt\":").append(c[8]).append(',')
          .append("\"png\":").append(c[9]).append(',')
          .append("\"csv\":").append(c[10]).append(',')
          .append("\"dll\":").append(c[11]).append(',')
          .append("\"other\":").append(c[12])
          .append('}');
        return sb.toString();
    }

    private static String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private void writeSettings() {
        int fps = 30, preset = 0, sensitivity = 50;
        boolean showFps = false, vsync = true;
        android.content.SharedPreferences p =
                getSharedPreferences("launcher", MODE_PRIVATE);
        fps = p.getInt("fps", 30);
        preset = p.getInt("touch_preset", 0);
        sensitivity = p.getInt("sensitivity", 50);
        showFps = p.getBoolean("show_fps", false);
        vsync = p.getBoolean("vsync", true);

        String ini = "fps=" + fps + "\n"
                + "touch_preset=" + preset + "\n"
                + "sensitivity=" + sensitivity + "\n"
                + "show_fps=" + (showFps ? 1 : 0) + "\n"
                + "vsync=" + (vsync ? 1 : 0) + "\n"
                + "asset_dir=" + assetDirForIni() + "\n"
                + "asset_uri=" + assetUriForIni() + "\n"
                + "use_game_elf=" + (p.getBoolean("use_game_elf", true) ? 1 : 0) + "\n"
                + "game_elf_path=" + p.getString("game_elf_path", DEFAULT_GAME_ELF_PATH) + "\n";
        try {
            java.io.FileOutputStream out = new java.io.FileOutputStream(settingsFile());
            out.write(ini.getBytes(StandardCharsets.UTF_8));
            out.close();
        } catch (java.io.IOException e) {
            android.util.Log.e("Launcher", "cannot write settings.ini", e);
        }
    }

    private void showSettingsDialog() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(24, 16, 24, 16);

        TextView t = new TextView(this);
        t.setText("Pengaturan (FPS/Controls disimpan otomatis)");
        t.setTextSize(12);
        root.addView(t);

        Button btnLog = new Button(this);
        btnLog.setText("Baca Log Terakhir");
        btnLog.setOnClickListener(v -> showLogDialog());
        root.addView(btnLog);

        // --- DEBUG: boot engine dari ELF milik user sendiri -----------------
        final android.content.SharedPreferences p =
                getSharedPreferences("launcher", MODE_PRIVATE);

        final Switch swElf = new Switch(this);
        swElf.setText("Boot game ELF (debug)");
        swElf.setTextSize(12);
        swElf.setChecked(p.getBoolean("use_game_elf", true));
        root.addView(swElf);

        TextView tvElfHint = new TextView(this);
        tvElfHint.setText("Path ELF (letakkan file di folder Download):");
        tvElfHint.setTextSize(11);
        root.addView(tvElfHint);

        final EditText etElfPath = new EditText(this);
        etElfPath.setText(p.getString("game_elf_path", DEFAULT_GAME_ELF_PATH));
        etElfPath.setTextSize(11);
        etElfPath.setSingleLine(true);
        root.addView(etElfPath);

        final TextView tvElfStatus = new TextView(this);
        tvElfStatus.setTextSize(11);
        root.addView(tvElfStatus);

        File elfFile = new File(etElfPath.getText().toString().trim());
        tvElfStatus.setText(elfFile.isFile()
                ? "✅ ELF ditemukan (" + (elfFile.length() / (1024 * 1024)) + " MB)"
                : "⚠️ ELF belum ada di path itu (boot akan dilewati)");

        new AlertDialog.Builder(this)
                .setTitle("Pengaturan")
                .setView(root)
                .setPositiveButton("Simpan", (d, w) -> {
                    String path = etElfPath.getText().toString().trim();
                    if (path.isEmpty()) path = DEFAULT_GAME_ELF_PATH;
                    p.edit()
                            .putBoolean("use_game_elf", swElf.isChecked())
                            .putString("game_elf_path", path)
                            .apply();
                    writeSettings();
                    if (swElf.isChecked() && !new File(path).isFile()) {
                        Toast.makeText(this,
                                "use_game_elf=1 tapi ELF tidak ditemukan:\n" + path,
                                Toast.LENGTH_LONG).show();
                    } else {
                        Toast.makeText(this, "Pengaturan disimpan", Toast.LENGTH_SHORT).show();
                    }
                })
                .setNegativeButton("Batal", null)
                .show();
    }

    private void showAboutDialog() {
        new AlertDialog.Builder(this)
                .setTitle("Tentang")
                .setMessage("Uncharted: Golden Abyss — Port\nTitle ID: PCSA00029\n\nScaffolding port tidak resmi. Konten game tidak didistribusikan.")
                .setPositiveButton("Tutup", null)
                .show();
    }

    private void showLogDialog() {
        String text;
        File f = logFile();
        if (!f.exists()) {
            text = "recomp.log belum ada.\n\nJalankan game minimal sekali.";
        } else {
            try {
                byte[] buf = new byte[(int) Math.min(f.length(), 64 * 1024)];
                FileInputStream in = new FileInputStream(f);
                int total = 0, n;
                while (total < buf.length && (n = in.read(buf, total, buf.length - total)) > 0) {
                    total += n;
                }
                in.close();
                text = new String(buf, 0, total, StandardCharsets.UTF_8);
            } catch (java.io.IOException e) {
                text = "Gagal membaca log: " + e.getMessage();
            }
        }
        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextSize(10);
        tv.setTextIsSelectable(false); // [NO_SELECT] konsisten: tidak ada teks yang bisa disalin
        tv.setTypeface(android.graphics.Typeface.MONOSPACE);
        tv.setPadding(16, 12, 16, 12);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(tv);

        new AlertDialog.Builder(this)
                .setTitle("Log Terakhir")
                .setView(scroll)
                .setPositiveButton("Tutup", null)
                .show();
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateStartGate();
        String dir = assetDirForIni();
        if (dir != null && !dir.trim().isEmpty()) {
            startFolderScan(dir);
        }
    }

    // ------------------------------------------------------------------
    // JavascriptBridge — Native Bridge dari UI.html ke Java
    // ------------------------------------------------------------------
    private static class JavascriptBridge {
        private final NativeBridge bridge;
        JavascriptBridge(NativeBridge b) { this.bridge = b; }
        @JavascriptInterface
        public void pickFolder() { bridge.pickFolder(); }
        @JavascriptInterface
        public void startGame() { bridge.startGame(); }
        @JavascriptInterface
        public void updatePath(String path) { bridge.updatePath(path); }
        @JavascriptInterface
        public void showToast(String message) { bridge.showToast(message); }
        @JavascriptInterface
        public void exitGame() { bridge.exitGame(); }
        @JavascriptInterface
        public String readLog() { return bridge.readLog(); }
        @JavascriptInterface
        public void rescanFolder() { bridge.rescanFolder(); }
        @JavascriptInterface
        public String getFolderStatus() { return bridge.getFolderStatus(); }
    }
}