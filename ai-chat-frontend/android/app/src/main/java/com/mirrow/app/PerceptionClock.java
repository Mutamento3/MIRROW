package com.mirrow.app;

import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Locale;

/** Shared local-time boundaries for phone perception totals. */
public final class PerceptionClock {
    public static final int LOGICAL_DAY_START_HOUR = 5;

    private PerceptionClock() {}

    public static long logicalDayStartMillis() {
        Calendar now = Calendar.getInstance();
        Calendar start = (Calendar) now.clone();
        start.set(Calendar.HOUR_OF_DAY, LOGICAL_DAY_START_HOUR);
        start.set(Calendar.MINUTE, 0);
        start.set(Calendar.SECOND, 0);
        start.set(Calendar.MILLISECOND, 0);
        if (now.before(start)) {
            start.add(Calendar.DAY_OF_MONTH, -1);
        }
        return start.getTimeInMillis();
    }

    public static String logicalDate() {
        Calendar logical = Calendar.getInstance();
        if (logical.get(Calendar.HOUR_OF_DAY) < LOGICAL_DAY_START_HOUR) {
            logical.add(Calendar.DAY_OF_MONTH, -1);
        }
        return new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(logical.getTime());
    }
}
