package com.mirrow.xhssignals;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.os.Process;
import android.content.pm.PackageManager;
import com.mirrow.xhssignals.protocol.SignalProtocol;
import java.util.ArrayList;

/** Signature-gated cross-package provider with an additional exact caller-UID allowlist. */
public final class SignalProvider extends ContentProvider {
    @Override public boolean onCreate() {
        SignalJournal.get(getContext());
        return true;
    }

    @Override public Bundle call(String method, String arg, Bundle extras) {
        if (!callerAllowed()) return SignalJournal.error("caller_rejected");
        try {
            SignalJournal journal = SignalJournal.get(getContext());
            if ("notifications".equals(method) && arg == null && extras == null) {
                String availability = XhsNotificationListener.readAvailability(getContext());
                return journal.snapshot(availability);
            }
            if ("ack".equals(method) && arg == null && extras != null
                    && extras.containsKey("signal_ids_b64")
                    && extras.size() == (extras.containsKey("full_scan_token") ? 2 : 1)) {
                ArrayList<String> ids = SignalProtocol.decodeIds(extras.getString("signal_ids_b64"));
                String token = extras.getString("full_scan_token");
                if (extras.containsKey("full_scan_token") && (token == null || !token.matches("[a-f0-9]{32}")))
                    return SignalJournal.error("invalid_arguments");
                // A disconnected interval cannot be declared fully scanned by a stale ack.
                // Check the live connection without replaying active rows into a full queue.
                // Replaying omitted rows here could invalidate every otherwise-current scan token.
                if (token != null && !"connected".equals(XhsNotificationListener.readAvailability(getContext(), false)))
                    return SignalJournal.error("listener_unavailable");
                return journal.ack(ids, token);
            }
            return SignalJournal.error("invalid_operation");
        } catch (Exception ignored) { return SignalJournal.error("signal_operation_failed"); }
    }

    private boolean callerAllowed() {
        int uid = Binder.getCallingUid();
        if (uid == Process.myUid()) return true;
        try {
            getContext().enforceCallingOrSelfPermission(SignalProtocol.PERMISSION, "Signal caller rejected");
            PackageManager packages = getContext().getPackageManager();
            return packages.getApplicationInfo(SignalProtocol.INPUT_PACKAGE, 0).uid == uid
                && packages.checkSignatures(SignalProtocol.INPUT_PACKAGE,
                    SignalProtocol.HOST_PACKAGE) == PackageManager.SIGNATURE_MATCH;
        } catch (Exception ignored) { return false; }
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        throw new UnsupportedOperationException();
    }
    @Override public String getType(Uri uri) { return null; }
    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) {
        throw new UnsupportedOperationException();
    }
}

