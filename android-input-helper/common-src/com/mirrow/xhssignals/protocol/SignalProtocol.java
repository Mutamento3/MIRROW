package com.mirrow.xhssignals.protocol;

import android.os.Bundle;
import org.json.JSONArray;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Set;

/** Shared bounded wire validation only; no journal, components, or storage access. */
public final class SignalProtocol {
    public static final String HOST_PACKAGE = "com.mirrow.xhssignals";
    public static final String INPUT_PACKAGE = "com.mirrow.xhstexthelper";
    public static final String AUTHORITY = "com.mirrow.xhssignals.signals";
    public static final String PERMISSION = "com.mirrow.xhssignals.permission.ACCESS_SIGNALS";

    private SignalProtocol() { }

    public static Bundle error(String code) {
        Bundle result = new Bundle();
        result.putString("status", "error");
        result.putString("error", code);
        result.putString("availability", "unknown");
        result.putBoolean("needs_full_scan", true);
        return result;
    }

    public static ArrayList<String> decodeIds(String encoded) throws Exception {
        if (encoded == null || encoded.length() > 24000 || encoded.length() % 4 != 0
                || !encoded.matches("[A-Za-z0-9+/]*={0,2}")) throw new IllegalArgumentException();
        byte[] bytes = android.util.Base64.decode(encoded, android.util.Base64.NO_WRAP);
        if (!android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP).equals(encoded))
            throw new IllegalArgumentException();
        String json = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
        org.json.JSONTokener tokener = new org.json.JSONTokener(json);
        Object parsed = tokener.nextValue();
        if (!(parsed instanceof JSONArray) || tokener.nextClean() != 0) throw new IllegalArgumentException();
        JSONArray values = (JSONArray) parsed;
        if (values.length() > 256) throw new IllegalArgumentException();
        ArrayList<String> result = new ArrayList<String>();
        Set<String> unique = new HashSet<String>();
        for (int i = 0; i < values.length(); i++) {
            Object value = values.get(i);
            if (!(value instanceof String) || !((String) value).matches("[a-f0-9]{64}")
                    || !unique.add((String) value)) throw new IllegalArgumentException();
            result.add((String) value);
        }
        return result;
    }
}
