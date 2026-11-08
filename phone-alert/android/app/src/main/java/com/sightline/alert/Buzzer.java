package com.sightline.alert;

import android.app.Notification;
import android.app.NotificationManager;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;

/**
 * Continuous buzz: the sound (res/raw/buzz_sound.mp3) on loop plus a repeating vibration, from
 * on() until off(). The laptop sends "buzz on" the moment the worker stops facing the hazard and
 * keeps refreshing it; it sends "buzz off" the moment they face it.
 *
 * Fail-safe: every on() carries a hold time. If no refresh arrives within it (laptop gone, Wi-Fi
 * dropped), the buzz stops by itself, so the phone never keeps buzzing on stale information.
 *
 * All calls hop onto the main thread, so the network thread can call on()/off() directly.
 */
final class Buzzer {
    static final int NOTIFICATION_ID = 2;
    private static final long[] PATTERN = {0, 400, 150};          // buzz 400 ms, pause 150 ms, repeat
    private static final long MIN_HOLD_MS = 300, MAX_HOLD_MS = 10000;

    private static final Handler main = new Handler(Looper.getMainLooper());
    private static MediaPlayer player;
    private static boolean active;
    private static long deadline;                                 // SystemClock.uptimeMillis()

    private Buzzer() {}

    /** Start (or keep) buzzing for at least holdMs more. */
    static void on(final Context c, final String title, final String message, final long holdMs) {
        main.post(() -> {
            deadline = SystemClock.uptimeMillis() + Math.max(MIN_HOLD_MS, Math.min(MAX_HOLD_MS, holdMs));
            main.removeCallbacks(WATCHDOG);
            main.postAtTime(WATCHDOG, deadline);
            if (active) {
                showNotification(c, title, message);              // update the text (distance, side)
                return;
            }
            active = true;
            startSound(c);
            startVibration();
            showNotification(c, title, message);
        });
    }

    /** Stop now. */
    static void off() {
        main.post(Buzzer::stopNow);
    }

    static boolean isActive() {
        return active;
    }

    private static final Runnable WATCHDOG = () -> {
        if (active && SystemClock.uptimeMillis() >= deadline) stopNow();
    };

    private static void stopNow() {
        main.removeCallbacks(WATCHDOG);
        if (!active) return;
        active = false;
        if (player != null) {
            try { player.stop(); } catch (Exception ignored) { }
            player.release();
            player = null;
        }
        Vibrator v = vibrator();
        if (v != null) v.cancel();
        Context c = App.ctx;
        if (c != null) c.getSystemService(NotificationManager.class).cancel(NOTIFICATION_ID);
    }

    private static void startSound(Context c) {
        try {
            AudioAttributes aa = new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build();
            int session = c.getSystemService(android.media.AudioManager.class).generateAudioSessionId();
            player = MediaPlayer.create(c, R.raw.buzz_sound, aa, session);
            if (player == null) return;
            player.setLooping(true);
            player.start();
        } catch (Exception e) {
            player = null;                                        // no sound, but the vibration still runs
        }
    }

    @SuppressWarnings("deprecation")
    private static Vibrator vibrator() {
        Context c = App.ctx;
        if (c == null) return null;
        try {
            return Build.VERSION.SDK_INT >= 31
                    ? c.getSystemService(VibratorManager.class).getDefaultVibrator()
                    : (Vibrator) c.getSystemService(Context.VIBRATOR_SERVICE);
        } catch (Exception e) {
            return null;
        }
    }

    private static void startVibration() {
        Vibrator v = vibrator();
        if (v == null) return;
        try {
            AudioAttributes aa = new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build();
            v.vibrate(VibrationEffect.createWaveform(PATTERN, 0), aa);    // repeat from index 0 until cancel()
        } catch (Exception ignored) { }
    }

    private static void showNotification(Context c, String title, String message) {
        try {
            Notification n = new Notification.Builder(c, Notifier.CH_BUZZ)
                    .setSmallIcon(R.drawable.ic_stat)
                .setColor(0xFFFACC15)                   // SightLine yellow
                    .setContentTitle(title)
                    .setContentText(message)
                    .setCategory(Notification.CATEGORY_ALARM)
                    .setOngoing(true)
                    .setOnlyAlertOnce(true)
                    .setContentIntent(Notifier.openApp(c))
                    .build();
            c.getSystemService(NotificationManager.class).notify(NOTIFICATION_ID, n);
        } catch (Exception ignored) { }
    }
}
