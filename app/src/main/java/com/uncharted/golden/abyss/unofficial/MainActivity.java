package com.uncharted.golden.abyss.unofficial;

import android.app.Activity;
import android.app.NativeActivity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.provider.DocumentsContract;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.Toast;

import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;

/**
 * Launcher for the unofficial port — berbasis WebView.
 *
 * Memuat file:///android_asset/index.html sebagai UI. JavaScript memanggil
 * Android.pickFolder() / Android.startGame() / Android.updatePath(path).
 */
public class MainActivity extends Activity {

    private static final String REPO_URL =
            "https://github.com/WPsepta/Uncharted-Golden-Abyss-Port-Unofficial.git";
    private static final int REQ_PICK_ASSETS = 4217;

    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        webView = new WebView(this);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        webView.addJavascriptInterface(new WebAppInterface(this), "Android");
        webView.loadUrl("file:///android_asset/index.html");
        setContentView(webView);

        // Buka repo saat footer "WPsepta" diklik? Ditangani di HTML (opsional).
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            String dir = assetDirForIni();
            if (dir != null && !dir.trim().isEmpty()) {
                webView.evaluateJavascript("updatePath('" + escapeJs(dir) + "')", null);
            }
        }
    }

    // ------------------------------------------------------------------
    // Asset folder picker (Storage Access Framework)
    // ------------------------------------------------------------------
    void openFolderPicker() {
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

        // Persist read access so the choice survives reboots.
        try {
            getContentResolver().takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (SecurityException e) {
            android.util.Log.e("Launcher", "persistable uri permission denied", e);
        }

        // Konversi SAF URI (content://) menjadi path file asli.
        String path = derivePrimaryPath(uri);
        if (path == null || path.trim().isEmpty()) {
            Toast.makeText(this, "Folder tidak bisa dipetakan ke path file. Pilih folder di storage internal (mis. /storage/emulated/0/...).", Toast.LENGTH_LONG).show();
            return;
        }

        android.content.SharedPreferences p = getSharedPreferences("launcher", MODE_PRIVATE);
        p.edit().putString("asset_uri", uri.toString()).apply();
        p.edit().putString("asset_dir", path).apply();

        // Kirim path absolut ke JavaScript.
        if (webView != null) {
            webView.evaluateJavascript("updatePath('" + escapeJs(path) + "')", null);
        }
        writeSettings();
    }

    /**
     * Best-effort mapping of a SAF tree URI ("primary:Download/uncharted") to a
     * real filesystem path ("/storage/emulated/0/Download/uncharted").
     */
    private static String derivePrimaryPath(Uri uri) {
        String docId;
        try {
            docId = DocumentsContract.getDocumentId(uri);
        } catch (IllegalArgumentException e) {
            return null;
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

    private void writeSettings() {
        int fps = 60;
        int preset = 0;
        int sensitivity = 50;
        boolean showFps = false;
        boolean vsync = true;

        android.content.SharedPreferences p =
                getSharedPreferences("launcher", MODE_PRIVATE);
        // Preserve previous numeric settings if present.
        fps = p.getInt("fps", 60);
        preset = p.getInt("touch_preset", 0);
        sensitivity = p.getInt("sensitivity", 50);
        showFps = p.getBoolean("show_fps", false);
        vsync = p.getBoolean("vsync", true);

        p.edit()
            .putInt("fps", fps)
            .putInt("touch_preset", preset)
            .putInt("sensitivity", sensitivity)
            .putBoolean("show_fps", showFps)
            .putBoolean("vsync", vsync)
            .apply();

        String ini = "fps=" + fps + "\n"
                + "touch_preset=" + preset + "\n"
                + "sensitivity=" + sensitivity + "\n"
                + "show_fps=" + (showFps ? 1 : 0) + "\n"
                + "vsync=" + (vsync ? 1 : 0) + "\n"
                + "asset_dir=" + assetDirForIni() + "\n"
                + "asset_uri=" + assetUriForIni() + "\n";
        try {
            java.io.FileOutputStream out = new java.io.FileOutputStream(new File(getFilesDir(), "settings.ini"));
            out.write(ini.getBytes(StandardCharsets.UTF_8));
            out.close();
        } catch (java.io.IOException e) {
            android.util.Log.e("Launcher", "cannot write settings.ini", e);
        }
    }

    private static String escapeJs(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("'", "\\'");
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    // ------------------------------------------------------------------
    // JavaScript bridge
    // ------------------------------------------------------------------
    public class WebAppInterface {
        private final MainActivity activity;

        public WebAppInterface(MainActivity activity) {
            this.activity = activity;
        }

        @JavascriptInterface
        public void pickFolder() {
            activity.runOnUiThread(() -> activity.openFolderPicker());
        }

        @JavascriptInterface
        public void startGame() {
            activity.runOnUiThread(() -> {
                if (!activity.assetSelected()) {
                    Toast.makeText(activity, "Pilih folder aset game dulu", Toast.LENGTH_SHORT).show();
                    return;
                }
                activity.writeSettings();
                Intent i = new Intent(activity, NativeActivity.class);
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                activity.startActivity(i);
            });
        }

        @JavascriptInterface
        public void updatePath(String path) {
            // Dipanggil dari JS jika diperlukan; simpan path ke SharedPreferences.
            if (path == null) return;
            android.content.SharedPreferences p = activity.getSharedPreferences("launcher", MODE_PRIVATE);
            p.edit().putString("asset_dir", path).apply();
            if (activity.webView != null) {
                activity.webView.evaluateJavascript("updatePath('" + escapeJs(path) + "')", null);
            }
        }
    }
}