package com.mirrow.xhssignals;

import android.content.Context;
import android.util.AtomicFile;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.UUID;

/** Private, atomically persisted signal metadata. Owned only by the independent notification host. */
final class SignalJournal {
    private static final int MAX_PENDING = 256;
    private static final int MAX_ACKNOWLEDGED = 2048;
    private static final int MAX_FILE_BYTES = 2 * 1024 * 1024;
    private static SignalJournal instance;
    private final AtomicFile file;
    private final LinkedHashMap<String, JSONObject> pending = new LinkedHashMap<String, JSONObject>();
    private final LinkedHashSet<String> acknowledged = new LinkedHashSet<String>();
    private boolean needsFullScan = true;
    private String scanToken = token();
    private int overflowCount;
    private boolean persistenceOk = true;

    static synchronized SignalJournal get(Context context) {
        if (instance == null) instance = new SignalJournal(context.getApplicationContext());
        return instance;
    }

    private SignalJournal(Context context) {
        file = new AtomicFile(new File(context.getFilesDir(), "xhs-notification-signals-v1.json"));
        load();
        // There may have been an unobserved interval before this process started.
        gap();
    }

    private void load() {
        if (!file.getBaseFile().exists() && !new File(file.getBaseFile().getPath() + ".bak").exists()) return;
        try {
            byte[] bytes = file.readFully();
            if (bytes.length > MAX_FILE_BYTES) throw new IllegalArgumentException();
            JSONObject data = new JSONObject(new String(bytes, StandardCharsets.UTF_8));
            if (data.getInt("version") != 1) throw new IllegalArgumentException();
            JSONArray signals = data.getJSONArray("signals");
            if (signals.length() > MAX_PENDING) throw new IllegalArgumentException();
            for (int i = 0; i < signals.length(); i++) {
                JSONObject row = signals.getJSONObject(i);
                String id = row.getString("id");
                if (!hex(id) || !hex(row.getString("key_hash")) || !hex(row.getString("content_sha256"))
                        || row.getString("native_key").length() > 4096
                        || row.getLong("posted_at_ms") < 0L || row.getLong("observed_at_ms") < 0L
                        || pending.containsKey(id)) throw new IllegalArgumentException();
                pending.put(id, row);
            }
            JSONArray seen = data.getJSONArray("acknowledged");
            if (seen.length() > MAX_ACKNOWLEDGED) throw new IllegalArgumentException();
            for (int i = 0; i < seen.length(); i++) {
                String id = seen.getString(i);
                if (!hex(id) || pending.containsKey(id)) throw new IllegalArgumentException();
                acknowledged.add(id);
            }
            overflowCount = Math.max(0, data.optInt("overflow_count", 0));
        } catch (Exception ignored) {
            // A damaged journal is never equivalent to an empty successfully read inbox.
            pending.clear();
            acknowledged.clear();
            needsFullScan = true;
            persistenceOk = false;
        }
    }

    synchronized void gap() {
        needsFullScan = true;
        scanToken = token();
        persist();
    }

    synchronized void ensureGap() {
        if (!needsFullScan) gap();
    }

    synchronized boolean observe(String nativeKey, long posted, String digest) {
        try {
            if (nativeKey == null || nativeKey.length() > 4096 || posted < 0L || !hex(digest)) {
                gap();
                return false;
            }
            String id = sha256(new JSONArray().put("xhs-native-signal-v1").put(nativeKey)
                .put(posted).put(digest).toString());
            if (pending.containsKey(id) || acknowledged.contains(id)) return true;
            if (pending.size() >= MAX_PENDING) {
                if (overflowCount < Integer.MAX_VALUE) overflowCount++;
                gap(); // Retain all earlier pending rows; the omitted event requires a full UI scan.
                return false;
            }
            JSONObject row = new JSONObject();
            row.put("id", id);
            row.put("native_key", nativeKey);
            row.put("key_hash", sha256(nativeKey));
            row.put("posted_at_ms", posted);
            row.put("observed_at_ms", System.currentTimeMillis());
            row.put("content_sha256", digest);
            pending.put(id, row);
            persist();
            return persistenceOk;
        } catch (Exception ignored) {
            gap();
            return false;
        }
    }

    synchronized android.os.Bundle snapshot(String availability) {
        // A temporary I/O failure must not require a new notification to recover.
        // The pending rows stay in memory and full-scan admission remains conservative.
        if (!persistenceOk) persist();
        android.os.Bundle result = new android.os.Bundle();
        JSONArray rows = new JSONArray();
        long now = System.currentTimeMillis();
        try {
            for (JSONObject stored : pending.values()) {
                JSONObject row = new JSONObject();
                row.put("id", stored.getString("id"));
                row.put("key_hash", stored.getString("key_hash"));
                row.put("posted_at_ms", stored.getLong("posted_at_ms"));
                row.put("observed_at_ms", stored.getLong("observed_at_ms"));
                row.put("content_sha256", stored.getString("content_sha256"));
                long posted = stored.getLong("posted_at_ms");
                row.put("stale", posted <= 0L || posted > now + 60000L || now - posted > 86400000L);
                rows.put(row);
            }
            result.putString("status", "ok");
            result.putString("availability", persistenceOk ? availability : "unknown");
            result.putBoolean("needs_full_scan", needsFullScan || !persistenceOk);
            result.putString("full_scan_token", scanToken);
            result.putString("signals_json", rows.toString());
            result.putInt("signal_count", rows.length());
            result.putInt("overflow_count", overflowCount);
            result.putLong("observed_at_ms", now);
            return result;
        } catch (Exception ignored) {
            result.clear();
            result.putString("status", "error");
            result.putString("error", "signal_snapshot_unavailable");
            result.putString("availability", "unknown");
            result.putBoolean("needs_full_scan", true);
            return result;
        }
    }

    synchronized android.os.Bundle ack(ArrayList<String> ids, String fullScanToken) {
        android.os.Bundle result = new android.os.Bundle();
        LinkedHashMap<String, JSONObject> previousPending = new LinkedHashMap<String, JSONObject>(pending);
        LinkedHashSet<String> previousAcknowledged = new LinkedHashSet<String>(acknowledged);
        boolean previousNeedsFullScan = needsFullScan;
        int previousOverflowCount = overflowCount;
        try {
            for (String id : ids) {
                if (!pending.containsKey(id) && !acknowledged.contains(id)) {
                    return error("unknown_signal_id");
                }
            }
            if (fullScanToken != null && !fullScanToken.equals(scanToken)) return error("stale_scan_token");
            if (!persistenceOk) return error("signal_journal_unavailable");
            for (String id : ids) {
                pending.remove(id);
                acknowledged.add(id);
            }
            while (acknowledged.size() > MAX_ACKNOWLEDGED) {
                Iterator<String> oldest = acknowledged.iterator();
                oldest.next(); oldest.remove();
            }
            if (fullScanToken != null) {
                needsFullScan = false;
                overflowCount = 0;
            }
            persist();
            if (!persistenceOk) {
                pending.clear(); pending.putAll(previousPending);
                acknowledged.clear(); acknowledged.addAll(previousAcknowledged);
                needsFullScan = true;
                overflowCount = previousOverflowCount;
                return error("signal_journal_unavailable");
            }
            result.putString("status", "ok");
            result.putInt("acked_count", ids.size());
            result.putBoolean("needs_full_scan", needsFullScan);
            result.putString("full_scan_token", scanToken);
            return result;
        } catch (Exception ignored) {
            pending.clear(); pending.putAll(previousPending);
            acknowledged.clear(); acknowledged.addAll(previousAcknowledged);
            needsFullScan = previousNeedsFullScan;
            overflowCount = previousOverflowCount;
            return error("signal_ack_failed");
        }
    }

    private void persist() {
        FileOutputStream stream = null;
        try {
            JSONObject data = new JSONObject();
            JSONArray rows = new JSONArray();
            for (JSONObject row : pending.values()) rows.put(row);
            JSONArray seen = new JSONArray();
            for (String id : acknowledged) seen.put(id);
            data.put("version", 1).put("signals", rows).put("acknowledged", seen)
                .put("needs_full_scan", needsFullScan).put("full_scan_token", scanToken)
                .put("overflow_count", overflowCount);
            byte[] bytes = data.toString().getBytes(StandardCharsets.UTF_8);
            if (bytes.length > MAX_FILE_BYTES) throw new IllegalArgumentException();
            stream = file.startWrite();
            stream.write(bytes);
            file.finishWrite(stream);
            stream = null;
            persistenceOk = true;
        } catch (Exception ignored) {
            if (stream != null) file.failWrite(stream);
            persistenceOk = false;
            needsFullScan = true;
        }
    }

    static android.os.Bundle error(String code) {
        android.os.Bundle result = new android.os.Bundle();
        result.putString("status", "error");
        result.putString("error", code);
        result.putString("availability", "unknown");
        result.putBoolean("needs_full_scan", true);
        return result;
    }

    static boolean hex(String value) { return value != null && value.matches("[a-f0-9]{64}"); }

    static String sha256(String value) throws Exception {
        byte[] bytes = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder result = new StringBuilder(64);
        for (byte b : bytes) result.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return result.toString();
    }

    private static String token() { return UUID.randomUUID().toString().replace("-", ""); }
}


