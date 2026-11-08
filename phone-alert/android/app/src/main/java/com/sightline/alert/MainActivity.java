package com.sightline.alert;

import android.Manifest;
import android.app.Activity;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
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

/** One screen: server address, Start/Stop, Find server, a local test, and live status.
 *  Colours and the hazard tape follow the SightLine pitch deck (same as the laptop's control page). */
public class MainActivity extends Activity {
    private static final int BG = Color.parseColor("#393939");       // charcoal page
    private static final int CARD = Color.parseColor("#27272A");
    private static final int FIELD = Color.parseColor("#18181B");
    private static final int HEAD = Color.parseColor("#09090B");
    private static final int LINE = Color.parseColor("#52525B");
    private static final int TEXT = Color.parseColor("#FAFAFA");
    private static final int MUTED = Color.parseColor("#A1A1AA");
    private static final int YELLOW = Color.parseColor("#FACC15");
    private static final int AMBER = Color.parseColor("#FFBF00");
    private static final int CREAM = Color.parseColor("#FFF2CC");
    private static final int INK = Color.parseColor("#09090B");
    private static final int TAPE_Y = Color.parseColor("#FFCC4D");
    private static final int TAPE_D = Color.parseColor("#292F33");
    // status pills: background, text
    private static final int[] PILL_OFF = {Color.parseColor("#3F3F46"), Color.parseColor("#E4E4E7")};
    private static final int[] PILL_OK = {Color.parseColor("#DCFCE7"), Color.parseColor("#166534")};
    private static final int[] PILL_WAIT = {Color.parseColor("#FEF08A"), Color.parseColor("#854D0E")};
    private static final int[] PILL_BAD = {Color.parseColor("#FEE2E2"), Color.parseColor("#991B1B")};

    private static final Typeface DISPLAY = Typeface.create("sans-serif-condensed", Typeface.BOLD);
    private static final Typeface HEADING = Typeface.create("sans-serif-medium", Typeface.NORMAL);

    private Prefs prefs;
    private EditText server, token;
    private TextView status, last, hint, pill;
    private Button toggle, find;
    private View topInset;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = new Prefs(this);
        Notifier.ensureChannels(this);
        if (Build.VERSION.SDK_INT < 35) {                  // older Android draws its own bars: match the header
            getWindow().setStatusBarColor(HEAD);
            getWindow().setNavigationBarColor(BG);
        }
        setContentView(build());
    }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    /** Yellow-and-black diagonal hazard tape, as on every slide of the deck. */
    private static class Tape extends View {
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Path stripe = new Path();
        Tape(Context c) { super(c); }
        @Override protected void onDraw(Canvas c) {
            int w = getWidth(), h = getHeight();
            c.drawColor(TAPE_D);
            p.setColor(TAPE_Y);
            float band = h * 1.4f;                         // stripe width along the tape
            for (float x = -2 * h; x < w + h; x += 2 * band) {
                stripe.reset();
                stripe.moveTo(x, h);
                stripe.lineTo(x + band, h);
                stripe.lineTo(x + band + h, 0);
                stripe.lineTo(x + h, 0);
                stripe.close();
                c.drawPath(stripe, p);
            }
        }
    }

    private View tape(int heightDp) {
        View t = new Tape(this);
        t.setLayoutParams(new LinearLayout.LayoutParams(-1, dp(heightDp)));
        return t;
    }

    private TextView text(String s, int sp, Typeface face, int color) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(color);
        if (face != null) t.setTypeface(face);
        t.setLineSpacing(0, 1.15f);
        return t;
    }

    private GradientDrawable rounded(int fill, int stroke, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(radiusDp));
        g.setColor(fill);
        if (stroke != 0) g.setStroke(dp(1), stroke);
        return g;
    }

    /** The deck's small yellow uppercase label ("STATUS", "CONNECT TO THE LAPTOP"). */
    private TextView tag(String s) {
        TextView t = text(s.toUpperCase(), 11, DISPLAY, INK);
        t.setLetterSpacing(0.08f);
        t.setPadding(dp(9), dp(4), dp(9), dp(4));
        t.setBackground(rounded(YELLOW, 0, 6));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -2);
        lp.bottomMargin = dp(10);
        t.setLayoutParams(lp);
        return t;
    }

    private LinearLayout card() {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setPadding(dp(16), dp(16), dp(16), dp(16));
        c.setBackground(rounded(CARD, LINE, 16));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(14);
        c.setLayoutParams(lp);
        return c;
    }

    private Button button(String label, boolean primary) {
        Button bt = new Button(this);
        bt.setText(label);
        bt.setAllCaps(false);
        bt.setTextSize(16);
        bt.setTypeface(HEADING);
        bt.setStateListAnimator(null);
        styleButton(bt, primary ? YELLOW : FIELD, primary ? INK : TEXT, primary ? 0 : LINE);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(primary ? 56 : 50));
        lp.topMargin = dp(10);
        bt.setLayoutParams(lp);
        return bt;
    }

    private void styleButton(Button bt, int fill, int textColor, int stroke) {
        bt.setBackground(new RippleDrawable(ColorStateList.valueOf(0x33FFFFFF), rounded(fill, stroke, 12), null));
        bt.setTextColor(textColor);
    }

    private EditText field(String hintText, boolean password) {
        EditText e = new EditText(this);
        e.setHint(hintText);
        e.setSingleLine(true);
        e.setTextSize(17);
        e.setTextColor(TEXT);
        e.setHintTextColor(MUTED);
        e.setBackground(rounded(FIELD, LINE, 10));
        e.setPadding(dp(14), dp(12), dp(14), dp(12));
        e.setInputType(password ? (InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD)
                : (InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI));
        e.setTypeface(Typeface.DEFAULT);                   // password fields default to monospace
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(6);
        e.setLayoutParams(lp);
        return e;
    }

    private TextView label(String s) {
        TextView t = text(s, 13, HEADING, MUTED);
        t.setLayoutParams(topMargin(14));
        return t;
    }

    private View build() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);

        // Header: status-bar space, tape, then the wordmark on black
        topInset = new View(this);
        topInset.setBackgroundColor(HEAD);
        page.addView(topInset, new LinearLayout.LayoutParams(-1, 0));
        page.addView(tape(10));
        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.VERTICAL);
        head.setBackgroundColor(HEAD);
        head.setPadding(dp(20), dp(16), dp(20), dp(18));
        TextView mark = text("SIGHTLINE", 34, DISPLAY, AMBER);
        mark.setLetterSpacing(0.06f);
        mark.setIncludeFontPadding(false);
        head.addView(mark);
        TextView sub = text("ALERT  ·  WORN BY THE WORKER", 11, HEADING, CREAM);
        sub.setLetterSpacing(0.14f);
        sub.setPadding(0, dp(6), 0, 0);
        head.addView(sub);
        page.addView(head);

        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(16), dp(4), dp(16), dp(20));
        page.addView(col);

        // Status card
        LinearLayout s = card();
        s.addView(tag("Status"));
        pill = text("", 13, DISPLAY, TEXT);
        pill.setLetterSpacing(0.06f);
        pill.setPadding(dp(12), dp(5), dp(12), dp(5));
        s.addView(pill, new LinearLayout.LayoutParams(-2, -2));
        status = text("", 17, HEADING, TEXT);
        status.setPadding(0, dp(10), 0, 0);
        s.addView(status);
        last = text("", 14, null, MUTED);
        last.setPadding(0, dp(6), 0, 0);
        s.addView(last);
        col.addView(s);

        // Connection card
        LinearLayout c = card();
        c.addView(tag("Connect to the laptop"));
        c.addView(text("Laptop address", 13, HEADING, MUTED));
        server = field("192.168.137.1:8765", false);
        server.setText(prefs.server());
        c.addView(server);
        hint = text("Use the address the laptop's control page shows. Port 8765 is added if you leave it out.", 12, null, MUTED);
        hint.setPadding(0, dp(6), 0, 0);
        c.addView(hint);
        c.addView(label("Token (only if the server uses one)"));
        token = field("leave empty if none", true);
        token.setText(prefs.token());
        c.addView(token);
        toggle = button("Start receiving alerts", true);
        LinearLayout.LayoutParams tlp = (LinearLayout.LayoutParams) toggle.getLayoutParams();
        tlp.topMargin = dp(16);
        c.addView(toggle);
        find = button("Find the laptop on this network", false);
        c.addView(find);
        col.addView(c);

        // Checks card
        LinearLayout k = card();
        k.addView(tag("Checks"));
        Button test = button("Test buzz on this phone", false);
        ((LinearLayout.LayoutParams) test.getLayoutParams()).topMargin = 0;
        k.addView(test);
        Button battery = button("Allow running in the background", false);
        k.addView(battery);
        TextView tip = text("On a laptop hotspot, the laptop is usually 192.168.137.1. "
                + "If alerts stop when the screen is off, press \"Allow running in the background\" and choose Unrestricted.",
                12, null, MUTED);
        tip.setPadding(0, dp(12), 0, 0);
        k.addView(tip);
        col.addView(k);

        View bottom = tape(10);
        ((LinearLayout.LayoutParams) bottom.getLayoutParams()).topMargin = dp(20);
        col.addView(bottom);

        toggle.setOnClickListener(v -> onToggle());
        find.setOnClickListener(v -> onFind());
        test.setOnClickListener(v -> {
            ensureNotificationPermission();
            Notifier.post(this, "Test alert", "If you felt a buzz and saw this, alerts work.", "alert");
        });
        battery.setOnClickListener(v -> startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)));

        ScrollView sv = new ScrollView(this);
        sv.setFillViewport(true);
        sv.addView(page);
        sv.setBackgroundColor(BG);
        if (Build.VERSION.SDK_INT >= 30) {                 // edge-to-edge: keep content clear of the bars
            sv.setOnApplyWindowInsetsListener((v, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                v.setPadding(bars.left, 0, bars.right, bars.bottom);
                topInset.getLayoutParams().height = bars.top;   // the black header runs up behind the status bar
                topInset.requestLayout();
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

    private void setPill(String label, int[] colors) {
        pill.setText(label);
        pill.setTextColor(colors[1]);
        pill.setBackground(rounded(colors[0], 0, 999));
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
        toggle.setText(on ? "Stop receiving alerts" : "Start receiving alerts");
        if (on) styleButton(toggle, PILL_BAD[0], PILL_BAD[1], 0);
        else styleButton(toggle, YELLOW, INK, 0);
        String st = AlertService.status;
        if (!on) setPill("STOPPED", PILL_OFF);
        else if (st.startsWith("Connected")) setPill("CONNECTED", PILL_OK);
        else if (st.startsWith("Connecting")) setPill("CONNECTING", PILL_WAIT);
        else setPill("PROBLEM", PILL_BAD);
        String s = on ? st : "Stopped. Press Start to receive alerts.";
        if (!getSystemService(NotificationManager.class).areNotificationsEnabled()) {
            s += "\nNotifications are off for this app. Allow them in Settings.";
            setPill("NOTIFICATIONS OFF", PILL_BAD);
        }
        status.setText(s);
        String la = AlertService.lastAlert;
        last.setText(la.isEmpty() ? "" : "Last alert: " + la);
        last.setVisibility(la.isEmpty() ? View.GONE : View.VISIBLE);
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
