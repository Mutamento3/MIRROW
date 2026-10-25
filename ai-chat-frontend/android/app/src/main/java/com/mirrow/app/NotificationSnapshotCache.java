package com.mirrow.app;

import java.util.*;

/** Notification identity/time bookkeeping, independent of Android and music parsing. */
public final class NotificationSnapshotCache {
    public static final long FRESH_WINDOW_MS = 30 * 60_000L;
    private final Map<String, String[]> entries = new LinkedHashMap<>();

    public synchronized void clear() { entries.clear(); }

    public synchronized void remove(String key) { entries.remove(key); }

    public synchronized void put(String key, String pkg, String title, String text,
                                 long postedAt, String category, boolean ongoing) {
        // Reposting/updating the same Android key replaces it, never increments a counter.
        entries.put(key, new String[]{pkg, title, text, String.valueOf(postedAt),
                category, ongoing ? "1" : "0"});
    }

    public synchronized String[][] recent(long now) {
        Map<String, String[]> latest = new HashMap<>();
        Map<String, Integer> counts = new HashMap<>();
        for (String[] value : entries.values()) {
            long posted = Long.parseLong(value[3]);
            if (posted <= 0 || posted > now + 60_000L) continue;
            // A live ongoing notification can span a long call/delivery. Old ordinary
            // notifications remain on the phone but are not recent observations.
            if (now - posted > FRESH_WINDOW_MS && !"1".equals(value[5])) continue;
            String pkg = value[0];
            counts.put(pkg, counts.getOrDefault(pkg, 0) + 1);
            String[] old = latest.get(pkg);
            if (old == null || posted >= Long.parseLong(old[3])) latest.put(pkg, value);
        }
        List<String[]> ordered = new ArrayList<>(latest.values());
        ordered.sort((a, b) -> Long.compare(Long.parseLong(b[3]), Long.parseLong(a[3])));
        List<String[]> result = new ArrayList<>();
        for (String[] v : ordered.subList(0, Math.min(20, ordered.size()))) {
            result.add(new String[]{v[0], v[1], v[2], v[3], v[4], v[5],
                    String.valueOf(counts.get(v[0]))});
        }
        return result.toArray(new String[0][]);
    }

    public synchronized String[] activeCallPackages() {
        Set<String> packages = new LinkedHashSet<>();
        for (String[] v : entries.values()) {
            // CATEGORY_CALL alone also describes missed/finished calls.
            if ("call".equals(v[4]) && "1".equals(v[5])) packages.add(v[0]);
        }
        return packages.toArray(new String[0]);
    }
}
