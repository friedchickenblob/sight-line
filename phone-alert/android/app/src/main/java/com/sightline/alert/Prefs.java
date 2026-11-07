package com.sightline.alert;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;

/** The few settings the app keeps. */
final class Prefs {
    static final int DEFAULT_PORT = 8765;
    private final SharedPreferences p;

    Prefs(Context c) {
        p = c.getApplicationContext().getSharedPreferences("alert", Context.MODE_PRIVATE);
    }

    String server() { return p.getString("server", ""); }
    String token() { return p.getString("token", ""); }
    boolean enabled() { return p.getBoolean("enabled", false); }
    long lastId() { return p.getLong("last_id", 0); }
    String boot() { return p.getString("boot", ""); }

    String deviceName() {
        String n = p.getString("device", "");
        return n.isEmpty() ? Build.MODEL : n;
    }

    void setServer(String s) { p.edit().putString("server", s).apply(); }
    void setToken(String s) { p.edit().putString("token", s).apply(); }
    void setEnabled(boolean b) { p.edit().putBoolean("enabled", b).apply(); }
    void setLast(long id, String boot) { p.edit().putLong("last_id", id).putString("boot", boot).apply(); }

    /** "192.168.137.1" or "192.168.137.1:8765" or "http://192.168.137.1:8765/" -> "192.168.137.1:8765". */
    static String normalise(String raw) {
        String s = raw == null ? "" : raw.trim();
        if (s.startsWith("http://")) s = s.substring(7);
        if (s.startsWith("https://")) s = s.substring(8);
        int slash = s.indexOf('/');
        if (slash >= 0) s = s.substring(0, slash);
        if (s.isEmpty()) return "";
        return s.contains(":") ? s : s + ":" + DEFAULT_PORT;
    }
}
