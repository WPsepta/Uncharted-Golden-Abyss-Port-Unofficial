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
import android.widget.ArrayAdapter;
import android.widget.Button;
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

    private TextView tvAssetStatus;
    private Button btnStart;

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
        };

        findViewById(R.id.btnGithub).setOnClickListener(v -> {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(REPO_URL)));
        });

        tvAssetStatus = findViewById(R.id.tvAssetStatus);
        btnStart = findViewById(R.id.btnStart);

        // Initialize WebView with UI.html
        webView = findViewById(R.id.webView);
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setAllowFileAccess(true);
        webView.getSettings().setDomStorageEnabled(true);
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
                + "asset_uri=" + assetUriForIni() + "\n";
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

        new AlertDialog.Builder(this)
                .setTitle("Pengaturan")
                .setView(root)
                .setPositiveButton("Tutup", null)
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
        tv.setTextIsSelectable(true);
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
    }
}