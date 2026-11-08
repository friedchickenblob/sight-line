package com.sightline.alert;

import android.Manifest;
import android.app.Activity;
import android.app.NotificationManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** One screen: server address, Start/Stop, Find server, a local test, and live status. */
public class MainActivity extends Activity {
    private static final int NAVY = Color.parseColor("#14213D");
    private static final int AMBER = Color.parseColor("#F2B705");

    private Prefs prefs;
    private EditText server, token;
    private TextView status, last, hint;
    private Button toggle, find;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = new Prefs(this);
        Notifier.ensureChannels(this);
        setContentView(build());
    }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    private TextView text(String s, int sp, boolean bold, int color) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    private Button button(String label, boolean primary) {
        Button bt = new Button(this);
        bt.setText(label);
        bt.setAllCaps(false);
        bt.setTextSize(16);
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(10));
        g.setColor(primary ? NAVY : Color.parseColor("#E5E7EB"));
        bt.setBackground(g);
        bt.setTextColor(primary ? Color.WHITE : NAVY);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(52));
        lp.topMargin = dp(10);
        bt.setLayoutParams(lp);
        return bt;
    }

    private EditText field(String hintText, boolean password) {
        EditText e = new EditText(this);
        e.setHint(hintText);
        e.setSingleLine(true);
        e.setTextSize(17);
        e.setInputType(password ? (InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD)
                : (InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI));
        return e;
    }

    private View build() {
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(20), dp(24), dp(20), dp(24));

        TextView title = text("SightLine Alert", 26, true, NAVY);
        col.addView(title);
        TextView sub = text("Receives alerts from the laptop over Wi-Fi or a hotspot.", 14, false, Color.parseColor("#6B7280"));
        col.addView(sub);

        status = text("", 17, true, NAVY);
        status.setPadding(dp(14), dp(12), dp(14), dp(12));
        GradientDrawable sg = new GradientDrawable();
        sg.setCornerRadius(dp(10));
        sg.setColor(Color.parseColor("#FEF6D8"));
        sg.setStroke(dp(2), AMBER);
        status.setBackground(sg);
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(-1, -2);
        slp.topMargin = dp(18);
        col.addView(status, slp);

        last = text("", 14, false, Color.parseColor("#374151"));
        last.setPadding(dp(2), dp(8), dp(2), 0);
        col.addView(last);

        col.addView(text("Laptop address", 14, true, NAVY), topMargin(18));
        server = field("192.168.137.1:8765", false);
        server.setText(prefs.server());
        col.addView(server);
        hint = text("Use the address the server prints when it starts. Port 8765 is added if you leave it out.", 12, false, Color.parseColor("#6B7280"));
        col.addView(hint);

        col.addView(text("Token (only if the server uses one)", 14, true, NAVY), topMargin(14));
        token = field("leave empty if none", true);
        token.setText(prefs.token());
        col.addView(token);

        toggle = button("Start", true);
        col.addView(toggle);
        find = button("Find server on this network", false);
        col.addView(find);
        Button test = button("Test notification on this phone", false);
        col.addView(test);
        Button battery = button("Allow running in background", false);
        col.addView(battery);

        TextView tip = text("Tip: on a laptop hotspot, the laptop is usually 192.168.137.1. "
                + "If alerts stop when the screen is off, press \"Allow running in background\" and choose Unrestricted.",
                12, false, Color.parseColor("#6B7280"));
        col.addView(tip, topMargin(14));

        toggle.setOnClickListener(v -> onToggle());
        find.setOnClickListener(v -> onFind());
        test.setOnClickListener(v -> {
            ensureNotificationPermission();
            Notifier.post(this, "Test alert", "If you felt a buzz and saw this, alerts work.", "alert");
        });
        battery.setOnClickListener(v -> startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)));

        ScrollView sv = new ScrollView(this);
        sv.setFillViewport(true);
        sv.addView(col);
        sv.setBackgroundColor(Color.WHITE);
        if (Build.VERSION.SDK_INT >= 30) {                 // edge-to-edge: keep content clear of the bars
            sv.setOnApplyWindowInsetsListener((v, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                return insets;
            });
        }
        return sv;
    }

    private LinearLayout.LayoutParams topMargin(int dpv) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(dpv);
        return lp;
    }

    @Override protected void onStart() {
        super.onStart();
        AlertService.onStatus = this::refresh;
        refresh();
    }

    @Override protected void onStop() {
        AlertService.onStatus = null;
        super.onStop();
    }

    private void refresh() {
        boolean on = AlertService.running;
        toggle.setText(on ? "Stop" : "Start");
        String s = on ? AlertService.status : "Stopped. Press Start to receive alerts.";
        if (!getSystemService(NotificationManager.class).areNotificationsEnabled()) {
            s += "\nNotifications are off for this app. Allow them in Settings.";
        }
        status.setText(s);
        String la = AlertService.lastAlert;
        last.setText(la.isEmpty() ? "" : "Last alert: " + la);
    }

    private void saveFields() {
        prefs.setServer(Prefs.normalise(server.getText().toString()));
        prefs.setToken(token.getText().toString().trim());
        server.setText(prefs.server());
    }

    private void ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
    }

    @Override public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        super.onRequestPermissionsResult(code, perms, results);
        refresh();
    }

    private void onToggle() {
        if (AlertService.running) {
            startService(new Intent(this, AlertService.class).setAction(AlertService.ACTION_STOP));
            AlertService.running = false;
            refresh();
            return;
        }
        saveFields();
        ensureNotificationPermission();
        if (prefs.server().isEmpty()) {
            status.setText("Enter the laptop address, or press Find server.");
            return;
        }
        startForegroundService(new Intent(this, AlertService.class));
        AlertService.running = true;
        refresh();
    }

    private void onFind() {
        saveFields();
        final String tok = prefs.token();
        int port = Prefs.DEFAULT_PORT;
        String cur = prefs.server();
        if (cur.contains(":")) {
            try { port = Integer.parseInt(cur.substring(cur.indexOf(':') + 1)); } catch (NumberFormatException ignored) { }
        }
        final int p = port;
        find.setEnabled(false);
        status.setText("Looking for the server on this network…");
        new Thread(() -> {
            final String hit = Net.discover(getApplicationContext(), p, tok);
            runOnUiThread(() -> {
                find.setEnabled(true);
                if (hit != null) {
                    server.setText(hit);
                    prefs.setServer(hit);
                    status.setText("Found the server at " + hit + ". Press Start.");
                } else {
                    status.setText("No server found. Check that the server is running, the laptop firewall allows port "
                            + p + ", and both devices are on the same Wi-Fi or hotspot.");
                }
            });
        }, "discover").start();
    }
}
