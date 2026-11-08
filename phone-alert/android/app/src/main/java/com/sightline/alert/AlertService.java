package com.sightline.alert;

import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;

/**
 * Foreground service that holds one Server-Sent Events connection to the laptop and turns each
 * "alert" event into a notification plus a buzz, and "buzz" events into a continuous buzz
 * (sound + vibration) that runs until "buzz off" or its hold time runs out (Buzzer).
 * It reconnects on its own (the hotspot drops).
 */
public class AlertService extends Service {
    static final String ACTION_STOP = "com.sightline.alert.STOP";
    private static final int ONGOING_ID = 1;
    private static final int READ_TIMEOUT_MS = 15000;     // the server pings every 5 s

    /** What the screen shows. Read from the UI thread, written from the worker thread. */
    static volatile String status = "Stopped";
    static volatile Runnable onStatus;
    static volatile boolean running;
    static volatile String lastAlert = "";

    private final Handler main = new Handler(Looper.getMainLooper());
    private volatile boolean stopping;
    private volatile boolean gotHello;
    private volatile HttpURLConnection current;
    private Thread worker;
    private Prefs prefs;
    private PowerManager.WakeLock wake;
    private WifiManager.WifiLock wifiLock;

    @Override public void onCreate() {
        super.onCreate();
        prefs = new Prefs(this);
        Notifier.ensureChannels(this);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            prefs.setEnabled(false);
            shutdown();
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
            return START_NOT_STICKY;
        }
        prefs.setEnabled(true);
        startAsForeground("Starting…");
        if (worker == null || !worker.isAlive()) {
            stopping = false;
            running = true;
            acquireLocks();
            worker = new Thread(this::loop, "alert-connection");
            worker.start();
        }
        return START_STICKY;                // if Android kills us, restart; prefs say we were meant to run
    }

    private void startAsForeground(String text) {
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(ONGOING_ID, Notifier.ongoing(this, text), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(ONGOING_ID, Notifier.ongoing(this, text));
        }
    }

    private void acquireLocks() {
        try {
            PowerManager pm = getSystemService(PowerManager.class);
            wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "sightline:alert");
            wake.setReferenceCounted(false);
            wake.acquire();
            WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "sightline:alert");
            wifiLock.setReferenceCounted(false);
            wifiLock.acquire();
        } catch (Exception ignored) { }
    }

    private void releaseLocks() {
        try { if (wake != null && wake.isHeld()) wake.release(); } catch (Exception ignored) { }
        try { if (wifiLock != null && wifiLock.isHeld()) wifiLock.release(); } catch (Exception ignored) { }
    }

    private void shutdown() {
        stopping = true;
        running = false;
        HttpURLConnection c = current;
        if (c != null) {
            try { c.disconnect(); } catch (Exception ignored) { }
        }
        if (worker != null) worker.interrupt();
        Buzzer.off();
        releaseLocks();
        setStatus("Stopped");
    }

    @Override public void onDestroy() {
        shutdown();
        super.onDestroy();
    }

    private void setStatus(final String s) {
        status = s;
        try {
            getSystemService(NotificationManager.class).notify(ONGOING_ID, Notifier.ongoing(this, s));
        } catch (Exception ignored) { }
        final Runnable r = onStatus;
        if (r != null) main.post(r);
    }

    private void loop() {
        long backoff = 1000;
        while (!stopping) {
            String server = prefs.server();
            if (server.isEmpty()) {
                setStatus("No server address. Open the app and press Find server.");
                sleep(3000);
                continue;
            }
            try {
                setStatus("Connecting to " + server + "…");
                connectAndRead(server);
                setStatus("Disconnected. Retrying…");
            } catch (AuthError e) {
                setStatus("Wrong token for " + server);
                sleep(5000);
                continue;
            } catch (Exception e) {
                if (stopping) break;
                setStatus("Can't reach " + server + " (" + shortMsg(e) + "). Retrying…");
            }
            if (gotHello) { backoff = 1000; gotHello = false; }   // it did connect: retry quickly
            sleep(backoff);
            backoff = Math.min(backoff * 2, 5000);
        }
    }

    private static final class AuthError extends Exception {}

    private void connectAndRead(String server) throws Exception {
        String token = prefs.token();
        String q = "device=" + URLEncoder.encode(prefs.deviceName(), "UTF-8");
        HttpURLConnection c = Net.open(this, new URL("http://" + server + "/events?" + q));
        current = c;
        try {
            c.setConnectTimeout(4000);
            c.setReadTimeout(READ_TIMEOUT_MS);
            c.setRequestProperty("Accept", "text/event-stream");
            if (!token.isEmpty()) c.setRequestProperty("X-Token", token);
            if (prefs.lastId() > 0) c.setRequestProperty("Last-Event-ID", String.valueOf(prefs.lastId()));
            int code = c.getResponseCode();
            if (code == 401) throw new AuthError();
            if (code != 200) throw new java.io.IOException("HTTP " + code);

            BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), "UTF-8"));
            String event = "message", id = null, line;
            StringBuilder data = new StringBuilder();
            while (!stopping && (line = r.readLine()) != null) {
                if (line.isEmpty()) {
                    if (data.length() > 0) handle(event, data.toString(), server);
                    event = "message";
                    id = null;
                    data.setLength(0);
                    continue;
                }
                if (line.startsWith(":")) continue;
                int i = line.indexOf(':');
                String field = i < 0 ? line : line.substring(0, i);
                String value = i < 0 ? "" : line.substring(i + 1);
                if (value.startsWith(" ")) value = value.substring(1);
                if ("event".equals(field)) event = value;
                else if ("id".equals(field)) id = value;
                else if ("data".equals(field)) {
                    if (data.length() > 0) data.append('\n');
                    data.append(value);
                }
            }
        } finally {
            current = null;
            c.disconnect();
        }
    }

    private void handle(String event, String data, String server) {
        try {
            JSONObject o = new JSONObject(data);
            if ("hello".equals(event)) {
                String boot = o.optString("boot", "");
                if (!boot.equals(prefs.boot())) prefs.setLast(0, boot);   // server restarted: its ids start over
                gotHello = true;
                setStatus("Connected");
            } else if ("buzz".equals(event)) {
                // Continuous buzz from the vision server: {"on": true, "hold_s": 1.5, "title", "message"}
                // while the worker isn't facing a hazard (refreshed), {"on": false} the moment they are.
                if (o.optBoolean("on", false)) {
                    String title = o.optString("title", "Look at the hazard");
                    String message = o.optString("message", "");
                    if (!Buzzer.isActive()) lastAlert = title + (message.isEmpty() ? "" : ": " + message);
                    Buzzer.on(this, title, message, (long) (o.optDouble("hold_s", 1.5) * 1000));
                } else {
                    Buzzer.off();
                }
            } else if ("alert".equals(event)) {
                long id = o.optLong("id", 0);
                if (id <= prefs.lastId()) return;                          // already shown
                prefs.setLast(id, prefs.boot());
                String title = o.optString("title", "Alert");
                String message = o.optString("message", "");
                lastAlert = title + (message.isEmpty() ? "" : ": " + message);
                Notifier.post(this, title, message, o.optString("level", "alert"));
                setStatus("Connected");
            }
        } catch (Exception ignored) { }
    }

    private static String shortMsg(Exception e) {
        String m = e.getMessage();
        if (m == null) return e.getClass().getSimpleName();
        return m.length() > 60 ? m.substring(0, 60) : m;
    }

    private void sleep(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException ignored) { }
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
