package com.uncharted.golden.abyss.unofficial;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.NativeActivity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.provider.DocumentFile;
import android.provider.DocumentsContract;
import android.text.InputType;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;


import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;

/**
 * Launcher for the unofficial port.
 *
 * Hanya scaffolding: tidak ada konten game yang disertakan. Untuk bermain,
 * user harus memilih folder berisi dump game miliknya sendiri
 * (SAF folder picker); engine (android_main.cpp) membaca asset_dir dari
 * settings.ini dan melakukan indeks aset dari sana.
 */
public class MainActivity extends Activity {

    private static final String REPO_URL =
            "https://github.com/WPsepta/Uncharted-Golden-Abyss-Port-Unofficial.git";
    private static final int REQ_PICK_ASSETS = 4217;

    private RadioGroup rgFps;
    private Spinner spTouchPreset;
    private SeekBar sbSensitivity;
    private Switch swShowFps;
    private Switch swVSync;
    private Button btnStart;
    private Button btnPickAssets;
    private TextView tvAssetStatus;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        // GitHub WPsepta (pojok kanan bawah) -> open repository
        findViewById(R.id.btnGithub).setOnClickListener(v -> {
            Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(REPO_URL));
            startActivity(i);
        });

        rgFps = findViewById(R.id.rgFps);
        spTouchPreset = findViewById(R.id.spTouchPreset);
        sbSensitivity = findViewById(R.id.sbSensitivity);
        swShowFps = findViewById(R.id.swShowFps);
        swVSync = findViewById(R.id.swVSync);
        btnStart = findViewById(R.id.btnStart);
        btnPickAssets = findViewById(R.id.btnPickAssets);
        tvAssetStatus = findViewById(R.id.tvAssetStatus);

        String[] presets = {"Thumb Left (default)", "Thumb Right", "Classic D-pad + Face"};
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this,
                android.R.layout.simple_spinner_item, presets);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spTouchPreset.setAdapter(adapter);

        btnPickAssets.setOnClickListener(v -> openFolderPicker());
        btnStart.setOnClickListener(v -> {
            if (!assetSelected()) {
                Toast.makeText(this, "Pilih folder aset game dulu", Toast.LENGTH_SHORT).show();
                return;
            }
            writeSettings();
            Intent i = new Intent(this, android.app.NativeActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        });
        findViewById(R.id.btnLog).setOnClickListener(v -> showLogDialog());

        readSettings();
        updateStartGate();
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateStartGate();
    }

    // ------------------------------------------------------------------
    // Asset folder picker (Storage Access Framework)
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

        // Validasi folder dengan DocumentFile (pastikan bisa dibaca).
        DocumentFile tree = DocumentFile.fromTreeUri(this, uri);
        if (tree == null || !tree.exists() || !tree.isDirectory()) {
            Toast.makeText(this, "Folder tidak valid / tidak terbaca", Toast.LENGTH_SHORT).show();
            return;
        }

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

        tvAssetStatus.setText("Folder aset: " + path);
        writeSettings();          // keep settings.ini in sync immediately
        updateStartGate();
    }

    /**
     * Best-effort mapping of a SAF tree URI ("primary:Download/uncharted") to a
     * real filesystem path ("/storage/emulated/0/Download/uncharted").
     * Returns null when the volume cannot be mapped.
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
    // Settings (SharedPreferences <-> settings.ini for the native engine)
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
        int fps = p.getInt("fps", 30);
        int fpsId = fps == 60 ? R.id.fps60 : fps == 0 ? R.id.fpsUnlimited : R.id.fps30;
        rgFps.check(fpsId);
        spTouchPreset.setSelection(p.getInt("touch_preset", 0));
        sbSensitivity.setProgress(p.getInt("sensitivity", 50));
        swShowFps.setChecked(p.getBoolean("show_fps", false));
        swVSync.setChecked(p.getBoolean("vsync", true));
        String dir = p.getString("asset_dir", "");
        String uri = p.getString("asset_uri", "");
        if ((dir != null && !dir.isEmpty()) || (uri != null && !uri.isEmpty())) {
            tvAssetStatus.setText("Folder aset: " + (dir == null || dir.isEmpty() ? uri : dir));
        }
    }

    private void writeSettings() {
        int checked = rgFps.getCheckedRadioButtonId();
        int fps = checked == R.id.fps60 ? 60 : checked == R.id.fpsUnlimited ? 0 : 30;
        int preset = spTouchPreset.getSelectedItemPosition();
        int sensitivity = sbSensitivity.getProgress();
        boolean showFps = swShowFps.isChecked();
        boolean vsync = swVSync.isChecked();

        android.content.SharedPreferences p =
                getSharedPreferences("launcher", MODE_PRIVATE);
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
            java.io.FileOutputStream out = new java.io.FileOutputStream(settingsFile());
            out.write(ini.getBytes(StandardCharsets.UTF_8));
            out.close();
        } catch (java.io.IOException e) {
            android.util.Log.e("Launcher", "cannot write settings.ini", e);
        }
    }

    private void showLogDialog() {
        String text;
        File f = logFile();
        if (!f.exists()) {
            text = "recomp.log belum ada.\n\nJalankan game minimal sekali "
                 + "(tombol MULAI GAME) — log akan dibuat di " + f.getAbsolutePath();
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
                if (f.length() > buf.length) {
                    text = "… (log terpotong, " + f.length() + " byte total)\n\n" + text;
                }
            } catch (java.io.IOException e) {
                text = "Gagal membaca log: " + e.getMessage();
            }
        }

        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextSize(11);
        tv.setTextIsSelectable(true);
        tv.setTypeface(android.graphics.Typeface.MONOSPACE);
        tv.setPadding(32, 24, 32, 24);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(tv);

        new AlertDialog.Builder(this)
                .setTitle("Baca Log Terakhir")
                .setView(scroll)
                .setPositiveButton("Tutup", null)
                .show();
    }
}