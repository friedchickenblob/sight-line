package com.sightline.alert;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.media.AudioAttributes;
import android.os.Build;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;

/** Posts alert notifications and buzzes the phone. Levels match the server: info, warn, alert. */
final class Notifier {
    static final String CH_STATUS = "status_v1";
    static final String CH_INFO = "info_v1";
    static final String CH_WARN = "warn_v1";
    static final String CH_ALERT = "alert_v1";

    private static final long[] BUZZ_WARN = {0, 300, 150, 300};
    private static final long[] BUZZ_ALERT = {0, 500, 150, 500, 150, 900};

    private static int nextId = 1000;

    private Notifier() {}

    static void ensureChannels(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        AudioAttributes attrs = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_NOTIFICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build();

        NotificationChannel status = new NotificationChannel(CH_STATUS, "Connection status", NotificationManager.IMPORTANCE_LOW);
        status.setDescription("Shows that the app is connected to the laptop");
        status.setShowBadge(false);
        nm.createNotificationChannel(status);

        NotificationChannel info = new NotificationChannel(CH_INFO, "Info", NotificationManager.IMPORTANCE_DEFAULT);
        info.setSound(android.provider.Settings.System.DEFAULT_NOTIFICATION_URI, attrs);
        nm.createNotificationChannel(info);

        NotificationChannel warn = new NotificationChannel(CH_WARN, "Warnings", NotificationManager.IMPORTANCE_HIGH);
        warn.enableVibration(true);
        warn.setVibrationPattern(BUZZ_WARN);
        warn.setSound(android.provider.Settings.System.DEFAULT_NOTIFICATION_URI, attrs);
        nm.createNotificationChannel(warn);

        NotificationChannel alert = new NotificationChannel(CH_ALERT, "Alerts", NotificationManager.IMPORTANCE_HIGH);
        alert.enableVibration(true);
        alert.setVibrationPattern(BUZZ_ALERT);
        alert.setSound(android.provider.Settings.System.DEFAULT_NOTIFICATION_URI, attrs);
        alert.setBypassDnd(false);
        nm.createNotificationChannel(alert);
    }

    static PendingIntent openApp(Context c) {
        Intent i = new Intent(c, MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        return PendingIntent.getActivity(c, 0, i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    /** The always-on notification the foreground service needs. */
    static Notification ongoing(Context c, String text) {
        return new Notification.Builder(c, CH_STATUS)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle("SightLine Alert")
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(openApp(c))
                .build();
    }

    static void post(Context c, String title, String message, String level) {
        ensureChannels(c);
        String channel = "alert".equals(level) ? CH_ALERT : "warn".equals(level) ? CH_WARN : CH_INFO;
        Notification.Builder b = new Notification.Builder(c, channel)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(title)
                .setContentText(message)
                .setStyle(new Notification.BigTextStyle().bigText(message))
                .setAutoCancel(true)
                .setContentIntent(openApp(c))
                .setShowWhen(true);
        if (!"info".equals(level)) b.setCategory(Notification.CATEGORY_ALARM);
        c.getSystemService(NotificationManager.class).notify(nextId++, b.build());
        if (nextId > 9000) nextId = 1000;
        if (!"info".equals(level)) buzz("alert".equals(level) ? BUZZ_ALERT : BUZZ_WARN);
    }

    /** Direct vibration as well as the channel's: this phone stands in for the wristband. */
    @SuppressWarnings("deprecation")
    private static void buzz(long[] pattern) {
        // Needs a Context for the service lookup; use the application context stored by the service/activity.
        Context c = App.ctx;
        if (c == null) return;
        try {
            Vibrator v = Build.VERSION.SDK_INT >= 31
                    ? c.getSystemService(VibratorManager.class).getDefaultVibrator()
                    : (Vibrator) c.getSystemService(Context.VIBRATOR_SERVICE);
            AudioAttributes aa = new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build();
            v.vibrate(VibrationEffect.createWaveform(pattern, -1), aa);
        } catch (Exception ignored) { }
    }
}
