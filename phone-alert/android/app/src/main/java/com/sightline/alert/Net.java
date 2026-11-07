package com.sightline.alert;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.RouteInfo;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Local-network plumbing.
 *
 * Why this exists: when a phone joins a hotspot that has no internet (a laptop hotspot,
 * for example), Android marks the Wi-Fi "no internet" and quietly sends app traffic over
 * mobile data instead. A request to 192.168.x.x then never reaches the laptop. So we find
 * the Wi-Fi network ourselves and open every connection on it.
 */
final class Net {
    static final String SERVICE = "sightline-phone-alert";

    private Net() {}

    /** The Wi-Fi network, or null (for example when this phone IS the hotspot). */
    @SuppressWarnings("deprecation")
    static Network wifi(Context c) {
        ConnectivityManager cm = (ConnectivityManager) c.getSystemService(Context.CONNECTIVITY_SERVICE);
        for (Network n : cm.getAllNetworks()) {
            NetworkCapabilities nc = cm.getNetworkCapabilities(n);
            if (nc != null && nc.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return n;
        }
        return null;
    }

    static HttpURLConnection open(Context c, URL url) throws java.io.IOException {
        Network n = wifi(c);
        return (HttpURLConnection) (n != null ? n.openConnection(url) : url.openConnection());
    }

    /** Is something at host:port a phone-alert server? Returns its /health JSON or null. */
    static JSONObject probe(Context c, String hostPort, String token, int timeoutMs) {
        HttpURLConnection h = null;
        try {
            h = open(c, new URL("http://" + hostPort + "/health"));
            h.setConnectTimeout(timeoutMs);
            h.setReadTimeout(timeoutMs);
            if (token != null && !token.isEmpty()) h.setRequestProperty("X-Token", token);
            if (h.getResponseCode() != 200) return null;
            StringBuilder sb = new StringBuilder();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(h.getInputStream()))) {
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
            }
            JSONObject o = new JSONObject(sb.toString());
            return SERVICE.equals(o.optString("service")) ? o : null;
        } catch (Exception e) {
            return null;
        } finally {
            if (h != null) h.disconnect();
        }
    }

    /** Likely server addresses, best guesses first: gateways, then every host on each local /24. */
    static List<String> candidates(Context c) {
        Set<String> out = new LinkedHashSet<>();
        Set<String> subnets = new LinkedHashSet<>();
        try {
            ConnectivityManager cm = (ConnectivityManager) c.getSystemService(Context.CONNECTIVITY_SERVICE);
            Network w = wifi(c);
            LinkProperties lp = w != null ? cm.getLinkProperties(w) : null;
            if (lp != null) {
                for (RouteInfo r : lp.getRoutes()) {
                    InetAddress g = r.getGateway();
                    if (g instanceof Inet4Address && !g.isAnyLocalAddress()) out.add(g.getHostAddress());
                }
            }
        } catch (Exception ignored) { }
        out.add("192.168.137.1");                         // Windows Mobile Hotspot: the laptop's fixed address
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                String name = ni.getName();
                if (!ni.isUp() || ni.isLoopback() || name.startsWith("rmnet") || name.startsWith("ccmni")
                        || name.startsWith("v4-") || name.startsWith("tun") || name.startsWith("dummy")) continue;
                for (InterfaceAddress ia : ni.getInterfaceAddresses()) {
                    InetAddress a = ia.getAddress();
                    if (!(a instanceof Inet4Address) || !a.isSiteLocalAddress()) continue;
                    byte[] b = a.getAddress();
                    String prefix = (b[0] & 255) + "." + (b[1] & 255) + "." + (b[2] & 255) + ".";
                    subnets.add(prefix);
                    out.add(prefix + "1");
                }
            }
        } catch (Exception ignored) { }
        for (String prefix : subnets) {
            for (int i = 1; i < 255; i++) out.add(prefix + i);
        }
        return new ArrayList<>(out);
    }

    /** Looks for the server on the local network. Returns "ip:port" or null. Blocking: call off the main thread. */
    static String discover(final Context c, final int port, final String token) {
        List<String> hosts = candidates(c);
        final AtomicReference<String> found = new AtomicReference<>();
        ExecutorService pool = Executors.newFixedThreadPool(48);
        for (final String h : hosts) {
            pool.execute(new Runnable() {
                @Override public void run() {
                    if (found.get() != null) return;
                    String hp = h + ":" + port;
                    if (probe(c, hp, token, 700) != null) found.compareAndSet(null, hp);
                }
            });
        }
        pool.shutdown();
        try {
            pool.awaitTermination(40, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) { }
        pool.shutdownNow();
        return found.get();
    }
}
