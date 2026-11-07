package com.sightline.alert;

import android.app.Application;
import android.content.Context;

/** Keeps the application context so Notifier can vibrate without being handed one. */
public class App extends Application {
    static volatile Context ctx;

    @Override public void onCreate() {
        super.onCreate();
        ctx = getApplicationContext();
    }
}
