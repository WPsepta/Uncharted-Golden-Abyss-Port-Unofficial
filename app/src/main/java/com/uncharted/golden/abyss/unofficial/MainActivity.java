package com.uncharted.golden.abyss.unofficial;

import android.app.NativeActivity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.DocumentsContract;
import android.provider.Settings;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.RadioGroup;
import android.widget.SeekBar;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;
import androidx.fragment.app.Fragment;
import androidx.fragment.app.FragmentActivity;
import androidx.viewpager2.adapter.FragmentStateAdapter;
import androidx.viewpager2.widget.ViewPager2;

import com.google.android.material.tabs.TabLayout;
import com.google.android.material.tabs.TabLayoutMediator;

import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;

public class MainActivity extends AppCompatActivity {

    private static final String REPO_URL =
            "https://github.com/WPsepta/Uncharted-Golden-Abyss-Port-Unofficial.git";
    private static final int REQ_PICK_ASSETS = 4217;

    private TextView tvAssetStatus;
    private Button btnStart;
    private TextView tvLog;
    private View githubFooter;

    private RadioGroup rgFps;
    private Spinner spTouchPreset;
    private SeekBar sbSensitivity;
    private Switch swShowFps;
    private Switch swVSync;

    private ViewPager2 viewPager;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        ensureStoragePermission();

        findViewById(R.id.btnGithub).setOnClickListener(v -> {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(REPO_URL)));
        });

        githubFooter = findViewById(R.id.githubFooter);

        // Tabs
        String[] tabNames = {"Launcher", "Performance", "Controls", "System", "Info", "Log"};
        int[] tabLayouts = {
                R.layout.page_launcher,
                R.layout.page_perf,
                R.layout.page_ctrl,
                R.layout.page_sys,
                R.layout.page_info,
                R.layout.page_log
        };

        viewPager = findViewById(R.id.viewPager);
        TabLayout tabLayout = findViewById(R.id.tabLayout);
        viewPager.setAdapter(new PageAdapter(this, tabLayouts));
        new TabLayoutMediator(tabLayout, viewPager, (tab, position) -> tab.setText(tabNames[position])).attach();

        viewPager.registerOnPageChangeCallback(new ViewPager2.OnPageChangeCallback() {
            @Override
            public void onPageSelected(int position) {
                // GitHub logo hanya di tab Launcher (index 0).
                githubFooter.setVisibility(position == 0 ? View.VISIBLE : View.GONE);
                if (position == 5) refreshLog();
            }
        });

        readSettings();
        updateStartGate();
    }

    private void ensureStoragePermission() {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R) {
            if (!android.os.Environment.isExternalStorageManager()) {
                try {
                    startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
                } catch (Exception e) {
                    android.util.Log.e("Launcher", "cannot open storage settings", e);
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // PageFragment
    // ------------------------------------------------------------------
    public static class PageFragment extends Fragment {
        private static final String ARG_LAYOUT = "layout";
        public static PageFragment newInstance(int layoutRes) {
            PageFragment f = new PageFragment();
            Bundle b = new Bundle();
            b.putInt(ARG_LAYOUT, layoutRes);
            f.setArguments(b);
            return f;
        }

        @Override
        public View onCreateView(android.view.LayoutInflater inflater, android.view.ViewGroup container, Bundle savedInstanceState) {
            int layout = getArguments().getInt(ARG_LAYOUT);
            View v = inflater.inflate(layout, container, false);
            MainActivity a = (MainActivity) getActivity();
            if (a == null) return v;

            if (layout == R.layout.page_launcher) {
                a.tvAssetStatus = v.findViewById(R.id.tvAssetStatus);
                a.btnStart = v.findViewById(R.id.btnStart);
                Button btnPick = v.findViewById(R.id.btnPickAssets);
                btnPick.setOnClickListener(x -> a.openFolderPicker());
                a.btnStart.setOnClickListener(x -> a.startGame());
                a.updateStartGate();
                String dir = a.assetDirForIni();
                if (dir != null && !dir.isEmpty()) {
                    a.tvAssetStatus.setText("Folder aset: " + dir);
                }
            } else if (layout == R.layout.page_perf) {
                a.rgFps = v.findViewById(R.id.rgFps);
                a.sbSensitivity = v.findViewById(R.id.sbSensitivity);
                a.readSettingsInto(a);
            } else if (layout == R.layout.page_ctrl) {
                a.spTouchPreset = v.findViewById(R.id.spTouchPreset);
                a.swShowFps = v.findViewById(R.id.swShowFps);
                String[] presets = {"Thumb Left (default)", "Thumb Right", "Classic D-pad + Face"};
                ArrayAdapter<String> adapter = new ArrayAdapter<>(a, android.R.layout.simple_spinner_item, presets);
                adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
                a.spTouchPreset.setAdapter(adapter);
                a.readSettingsInto(a);
            } else if (layout == R.layout.page_sys) {
                a.swVSync = v.findViewById(R.id.swVSync);
                Button btnLog = v.findViewById(R.id.btnLog);
                btnLog.setOnClickListener(x -> a.showLog());
                a.readSettingsInto(a);
            } else if (layout == R.layout.page_log) {
                a.tvLog = v.findViewById(R.id.tvLog);
                a.refreshLog();
            }
            return v;
        }
    }

    public static class PageAdapter extends FragmentStateAdapter {
        private final int[] layouts;
        public PageAdapter(FragmentActivity fa, int[] layouts) {
            super(fa);
            this.layouts = layouts;
        }
        @Override
        public int getItemCount() { return layouts.length; }
        @Override
        public Fragment createFragment(int position) {
            return PageFragment.newInstance(layouts[position]);
        }
    }

    // ------------------------------------------------------------------
    // Asset folder picker
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

        if (tvAssetStatus != null) tvAssetStatus.setText("Folder aset: " + path);
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

    void updateStartGate() {
        boolean ok = assetSelected();
        if (btnStart != null) {
            btnStart.setEnabled(ok);
            btnStart.setAlpha(ok ? 1.0f : 0.4f);
        }
        if (ok && tvAssetStatus != null) {
            String dir = assetDirForIni();
            tvAssetStatus.setText("Folder aset: " + (dir.isEmpty() ? assetUriForIni() : dir));
        }
    }

    void startGame() {
        if (!assetSelected()) {
            Toast.makeText(this, "Pilih folder aset game dulu", Toast.LENGTH_SHORT).show();
            return;
        }
        writeSettings();
        Intent i = new Intent(this, NativeActivity.class);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(i);
    }

    void readSettings() { readSettingsInto(this); }

    void readSettingsInto(MainActivity a) {
        android.content.SharedPreferences p = a.getSharedPreferences("launcher", MODE_PRIVATE);
        int fps = p.getInt("fps", 30);
        if (a.rgFps != null) {
            int fpsId = fps == 60 ? R.id.fps60 : fps == 0 ? R.id.fpsUnlimited : R.id.fps30;
            a.rgFps.check(fpsId);
        }
        if (a.spTouchPreset != null) a.spTouchPreset.setSelection(p.getInt("touch_preset", 0));
        if (a.sbSensitivity != null) a.sbSensitivity.setProgress(p.getInt("sensitivity", 50));
        if (a.swShowFps != null) a.swShowFps.setChecked(p.getBoolean("show_fps", false));
        if (a.swVSync != null) a.swVSync.setChecked(p.getBoolean("vsync", true));
    }

    private void writeSettings() {
        int fps = 30, preset = 0, sensitivity = 50;
        boolean showFps = false, vsync = true;
        if (rgFps != null) {
            int checked = rgFps.getCheckedRadioButtonId();
            fps = checked == R.id.fps60 ? 60 : checked == R.id.fpsUnlimited ? 0 : 30;
        }
        if (spTouchPreset != null) preset = spTouchPreset.getSelectedItemPosition();
        if (sbSensitivity != null) sensitivity = sbSensitivity.getProgress();
        if (swShowFps != null) showFps = swShowFps.isChecked();
        if (swVSync != null) vsync = swVSync.isChecked();

        android.content.SharedPreferences p = getSharedPreferences("launcher", MODE_PRIVATE);
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

    void showLog() {
        File f = new File(getFilesDir(), "recomp.log");
        String text;
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
        if (tvLog != null) tvLog.setText(text);
    }

    void refreshLog() { showLog(); }

    @Override
    protected void onResume() {
        super.onResume();
        updateStartGate();
    }
}