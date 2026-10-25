package com.mirrow.app.relay;

import android.content.Context;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/**
 * Small local-only event journal for overnight relay diagnosis.
 *
 * It intentionally stores timestamps, state and tool names only: no sensor
 * payload, message body, IP address or location is copied here. The file is
 * app-private so it survives logcat rotation and can be pulled with run-as on
 * a debug build. A bounded size prevents an always-on service from growing it
 * without limit.
 */
public final class RelayObservabilityStore {
    private static final String FILE_NAME = "relay_observability.ndjson";
    private static final long MAX_BYTES = 1024L * 1024L;
    private static final Object LOCK = new Object();

    private RelayObservabilityStore() {}

    public static void record(Context context, String event, JSONObject fields) {
        if (context == null || event == null || event.isEmpty()) return;
        synchronized (LOCK) {
            try {
                File file = new File(context.getFilesDir(), FILE_NAME);
                trimIfNeeded(file);

                JSONObject line = new JSONObject();
                line.put("ts", System.currentTimeMillis());
                line.put("event", event.length() > 64 ? event.substring(0, 64) : event);
                if (fields != null) {
                    java.util.Iterator<String> keys = fields.keys();
                    while (keys.hasNext()) {
                        String key = keys.next();
                        line.put(key, fields.opt(key));
                    }
                }
                byte[] bytes = (line.toString() + "\n").getBytes(StandardCharsets.UTF_8);
                try (FileOutputStream out = new FileOutputStream(file, true)) {
                    out.write(bytes);
                    out.flush();
                }
            } catch (Exception ignored) {
                // Diagnostics must never affect relay work.
            }
        }
    }

    private static void trimIfNeeded(File file) {
        if (!file.exists() || file.length() <= MAX_BYTES) return;
        File temp = new File(file.getParentFile(), FILE_NAME + ".tmp");
        try {
            long keepFrom = Math.max(0L, file.length() - (MAX_BYTES / 2));
            try (FileInputStream in = new FileInputStream(file)) {
                long skipped = 0L;
                while (skipped < keepFrom) {
                    long step = in.skip(keepFrom - skipped);
                    if (step <= 0) break;
                    skipped += step;
                }
                // Start at the next complete NDJSON line.
                int first;
                while ((first = in.read()) != -1 && first != '\n') {}
                try (FileOutputStream out = new FileOutputStream(temp, false)) {
                    byte[] buffer = new byte[8192];
                    int count;
                    while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
                }
            }
            if (!temp.renameTo(file)) {
                // Best effort fallback; the next append remains valid even if
                // trimming loses this cycle on an OEM filesystem.
                temp.delete();
            }
        } catch (Exception ignored) {
            temp.delete();
        }
    }
}
