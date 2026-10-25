package com.mirrow.app.network;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;

import java.net.URI;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** 手机后端端点的持久化与故障轮换边界。 */
public final class BackendEndpointPool {
    public static final String PREFS_NAME = "mirrow_config";
    private static final String KEY_SELECTED = "backend_url";
    private static final String KEY_CANDIDATES = "backend_candidates";

    private BackendEndpointPool() {}

    public static synchronized String getSelected(Context context, String fallback) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        String saved = normalize(prefs.getString(KEY_SELECTED, ""));
        return saved.isEmpty() ? fallback : saved;
    }

    public static synchronized void save(Context context, String selected, JSONArray rawCandidates) {
        Set<String> unique = new LinkedHashSet<>();
        if (rawCandidates != null) {
            for (int i = 0; i < rawCandidates.length(); i++) {
                String candidate = normalize(rawCandidates.optString(i, ""));
                if (!candidate.isEmpty()) unique.add(candidate);
            }
        }
        String safeSelected = normalize(selected);
        if (!safeSelected.isEmpty()) unique.add(safeSelected);
        if (unique.isEmpty()) return;

        JSONArray stored = new JSONArray();
        for (String candidate : unique) stored.put(candidate);
        if (safeSelected.isEmpty()) safeSelected = unique.iterator().next();

        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_SELECTED, safeSelected)
            .putString(KEY_CANDIDATES, stored.toString())
            .apply();
    }

    public static synchronized String rotateAfterFailure(Context context, String current) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        List<String> candidates = readCandidates(prefs);
        if (candidates.size() < 2) return current;
        int index = candidates.indexOf(normalize(current));
        String next = candidates.get(index >= 0 ? (index + 1) % candidates.size() : 0);
        prefs.edit().putString(KEY_SELECTED, next).apply();
        return next;
    }

    public static synchronized void recordConnected(Context context, String selected) {
        String safe = normalize(selected);
        if (!safe.isEmpty()) {
            context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit().putString(KEY_SELECTED, safe).apply();
        }
    }

    private static List<String> readCandidates(SharedPreferences prefs) {
        Set<String> unique = new LinkedHashSet<>();
        try {
            JSONArray array = new JSONArray(prefs.getString(KEY_CANDIDATES, "[]"));
            for (int i = 0; i < array.length(); i++) {
                String candidate = normalize(array.optString(i, ""));
                if (!candidate.isEmpty()) unique.add(candidate);
            }
        } catch (Exception ignored) {}
        return new ArrayList<>(unique);
    }

    /** 只接受 8005 端口的 IP 字面量，避免运行时被扩展为任意网络访问。 */
    private static String normalize(String raw) {
        if (raw == null) return "";
        try {
            URI uri = URI.create(raw.trim());
            String scheme = uri.getScheme();
            String host = uri.getHost();
            if (!("http".equals(scheme) || "https".equals(scheme))) return "";
            if (host == null || !host.matches("[0-9a-fA-F:.]+")) return "";
            if (uri.getPort() != 8005 || uri.getRawUserInfo() != null || uri.getRawQuery() != null) return "";
            return scheme + "://" + host + ":" + uri.getPort();
        } catch (Exception ignored) {
            return "";
        }
    }
}
