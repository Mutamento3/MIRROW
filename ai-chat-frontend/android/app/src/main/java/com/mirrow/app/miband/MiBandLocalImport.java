package com.mirrow.app.miband;

import android.content.Context;
import com.mirrow.app.BuildConfig;
import org.json.JSONObject;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.util.concurrent.atomic.AtomicBoolean;

/** Debug-ADB one-shot import. No exported component, URL, intent credential or logging. */
public final class MiBandLocalImport {
    private static final AtomicBoolean BUSY = new AtomicBoolean(false);
    private MiBandLocalImport() {}

    public static void consume(Context context) {
        if (!BuildConfig.DEBUG || !BUSY.compareAndSet(false, true)) return;
        Context app = context.getApplicationContext();
        new Thread(() -> {
            File input = new File(app.getNoBackupFilesDir(), "miband-import.json");
            try {
                if (!input.isFile()) return;
                if (input.length() > 4096) throw new IllegalArgumentException();
                byte[] raw = new byte[(int) input.length()];
                try (FileInputStream stream = new FileInputStream(input)) {
                    int offset = 0, count;
                    while (offset < raw.length && (count = stream.read(raw, offset, raw.length-offset)) > 0) offset += count;
                    if (offset != raw.length) throw new IllegalArgumentException();
                }
                // Delete before changing configuration: failures cannot leave credentials staged.
                if (!input.delete()) throw new IllegalStateException();
                JSONObject data;
                try { data = new JSONObject(new String(raw, StandardCharsets.UTF_8)); }
                finally { java.util.Arrays.fill(raw, (byte) 0); }
                long expiry = data.getLong("expires_at_ms");
                if (expiry < System.currentTimeMillis() || expiry > System.currentTimeMillis()+300_000L) throw new IllegalArgumentException();
                MiBandRuntime runtime = MiBandBridge.attach(app);
                if (runtime.hasConfig() && !data.optBoolean("replace_existing", false)) {
                    receipt(app, "existing_config"); return;
                }
                runtime.saveConfig(data.getString("address"), "Xiaomi Smart Band 7", data.getString("key"), runtime.settings());
                data.remove("key");
                receipt(app, "saved");
                if (MiBandBridge.permitted(app)) runtime.manualReconnect();
            } catch (Exception ignored) {
                receipt(app, "invalid_or_unavailable");
            } finally {
                if (input.exists()) input.delete();
                BUSY.set(false);
            }
        }, "mirrow-band-local-import").start();
    }

    private static void receipt(Context context, String result) {
        try {
            try (FileOutputStream stream = new FileOutputStream(new File(context.getNoBackupFilesDir(), "miband-import-result.txt"))) {
                stream.write(result.getBytes(StandardCharsets.US_ASCII));
            }
        } catch (Exception ignored) { /* No diagnostic may include import contents. */ }
    }
}
