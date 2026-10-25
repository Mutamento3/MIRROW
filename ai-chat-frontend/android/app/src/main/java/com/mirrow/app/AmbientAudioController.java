package com.mirrow.app;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.NoiseSuppressor;
import android.os.SystemClock;
import android.util.Base64;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Native, process-armed ambient microphone recorder.
 *
 * Audio stays in the app-private cache, is retried with the same clip id, and
 * is deleted only after a terminal host receipt. No transcript or voiceprint is
 * produced on the phone. The arm is intentionally not persisted across process
 * death: Android's while-in-use microphone permission must be handed off by a
 * visible Activity each time the service process is created.
 */
final class AmbientAudioController {
    interface Sender { boolean send(JSONObject message); }
    interface ForegroundPromoter { boolean promote(boolean microphone); }
    interface ContextProvider { JSONObject snapshot(); }

    private static final String TAG = "AmbientAudio";
    private static final int SAMPLE_RATE = 16_000;
    private static final int FRAME_SAMPLES = 320; // 20 ms
    private static final int FRAME_BYTES = FRAME_SAMPLES * 2;
    private static final int SPEECH_THRESHOLD = 520;
    private static final int START_FRAMES = 3;
    private static final int SILENCE_FRAMES = 60; // 1.2 sec
    private static final int MIN_SEGMENT_FRAMES = 30; // 0.6 sec
    private static final int MAX_SEGMENT_FRAMES = 1500; // 30 sec
    private static final long MAX_LOCAL_AGE_MS = 24L * 60L * 60L * 1000L;
    private static final String PREF_CONFIG = "ambient_listening_config";

    private final Context context;
    private final SharedPreferences prefs;
    private final File cacheDir;
    private final Sender sender;
    private final ForegroundPromoter foregroundPromoter;
    private final ContextProvider contextProvider;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private volatile boolean armed = false;
    private volatile JSONObject config = new JSONObject();
    private volatile String inFlightClip = "";
    private Thread recordThread;
    private Thread policyThread;
    private volatile Boolean lastMediaActive;
    private AudioRecord audioRecord;
    private AcousticEchoCanceler echoCanceler;
    private NoiseSuppressor noiseSuppressor;
    private volatile boolean playbackSuppressionEnabled;
    private volatile long pcLeaseUntilElapsedMs;
    private volatile String selectedSource = "phone";

    AmbientAudioController(Context context, SharedPreferences prefs, Sender sender,
                           ForegroundPromoter foregroundPromoter,
                           ContextProvider contextProvider) {
        this.context = context.getApplicationContext();
        this.prefs = prefs;
        this.sender = sender;
        this.foregroundPromoter = foregroundPromoter;
        this.contextProvider = contextProvider;
        this.cacheDir = new File(context.getFilesDir(), "ambient_audio_cache");
        if (!cacheDir.exists()) cacheDir.mkdirs();
        try {
            String saved = prefs.getString(PREF_CONFIG, "");
            if (!saved.isEmpty()) config = new JSONObject(saved);
        } catch (Exception ignored) {}
        cleanupExpired();
    }

    synchronized void armFromVisibleActivity() {
        armed = context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
        if (armed) armed = foregroundPromoter.promote(true);
        checkpoint("permission", armed ? "success" : "blocked",
                armed ? "" : "record_audio_denied", false,
                armed ? "evaluate_policy" : "grant_microphone_permission");
        if (armed) startPolicyMonitor();
        applyPolicy();
    }

    synchronized void updateConfig(JSONObject next) {
        if (next == null) return;
        String previousSource = selectedSource;
        config = next;
        selectedSource = "pc".equals(next.optString("selected_source", "phone")) ? "pc" : "phone";
        long remaining = Math.max(0L, Math.min(30_000L,
                next.optLong("source_lease_until_ms", 0L)
                        - next.optLong("server_time_ms", 0L)));
        pcLeaseUntilElapsedMs = "pc".equals(selectedSource)
                ? SystemClock.elapsedRealtime() + remaining : 0L;
        prefs.edit().putString(PREF_CONFIG, next.toString()).apply();
        checkpoint("config", "success", "", false, "evaluate_policy");
        if (!selectedSource.equals(previousSource)) {
            checkpoint("source_selection", "success", selectedSource, false,
                    "apply_capture_source");
        }
        applyPolicy();
    }

    synchronized void onConnected() {
        inFlightClip = "";
        cleanupExpired();
        flushOne();
    }

    synchronized void onAck(JSONObject ack) {
        String clipId = ack == null ? "" : ack.optString("clip_id", "");
        if (clipId.isEmpty()) return;
        boolean terminal = ack.optBoolean("terminal", false);
        if (terminal && ack.optBoolean("delete_local", false)) {
            File target = findClip(clipId);
            if (target != null && !target.delete()) {
                Log.w(TAG, "failed to delete acknowledged clip");
            }
            if (target != null) new File(target.getAbsolutePath() + ".json").delete();
            checkpoint("receipt_ack", ack.optBoolean("accepted", false) ? "success" : "blocked",
                    ack.optString("error_code", ""), false, "local_cache_deleted");
        }
        if (clipId.equals(inFlightClip)) inFlightClip = "";
        flushOne();
    }

    synchronized boolean isRunning() { return running.get(); }
    synchronized boolean isArmed() { return armed; }
    boolean isPlaybackSuppressionEnabled() { return playbackSuppressionEnabled; }

    synchronized void close() {
        armed = false;
        if (policyThread != null) policyThread.interrupt();
        stopRecording("service_destroyed");
        foregroundPromoter.promote(false);
    }

    private synchronized void applyPolicy() {
        if (armed && context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            armed = false;
        }
        boolean allowed = computeLocalAllowed();
        if (armed && allowed) startRecording();
        else stopRecording(!armed ? "not_armed" : pcLeaseActive()
                ? "pc_source_lease" : "policy_blocked");
    }

    private boolean pcLeaseActive() {
        return "pc".equals(selectedSource)
                && SystemClock.elapsedRealtime() < pcLeaseUntilElapsedMs;
    }

    private boolean computeLocalAllowed() {
        try {
            String today = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date());
            if (!today.equals(config.optString("policy_local_date", ""))) return false;
            String mode = config.optString("mode", "smart");
            if ("off".equals(mode) || inPrivacyWindow(config.optJSONArray("privacy_windows"))) return false;
            if (!config.optBoolean("calendar_rest_day", false)) {
                String start = config.optString("work_start", "");
                String end = config.optString("work_end", "");
                if (!validTime(start) || !validTime(end)) return false;
                if (withinTime(start, end)) return false;
            }
            if (pcLeaseActive()) return false;
            if ("on".equals(mode)) return true;
            String behavior = config.optString("behavior_mode", "unknown");
            if ("away".equals(behavior) || "active".equals(behavior)) return true;
            if ("rest".equals(behavior)) {
                long lastPrivate = config.optLong("last_private_chat_at_ms", 0L);
                long idleThresholdMs = Math.max(60L,
                        config.optLong("idle_threshold_seconds", 600L)) * 1000L;
                return lastPrivate > 0L
                        && System.currentTimeMillis() - lastPrivate >= idleThresholdMs;
            }
        } catch (Exception ignored) {}
        return false;
    }

    private boolean phoneMediaActive() {
        try {
            AudioManager manager = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
            return manager != null && manager.isMusicActive();
        } catch (Exception ignored) {
            return false;
        }
    }

    private synchronized void startPolicyMonitor() {
        if (policyThread != null && policyThread.isAlive()) return;
        policyThread = new Thread(() -> {
            while (armed && !Thread.currentThread().isInterrupted()) {
                boolean active = phoneMediaActive();
                Boolean previous = lastMediaActive;
                if (previous == null || previous != active) {
                    lastMediaActive = active;
                    checkpoint("media_playback", "success",
                            active ? "phone_media_active" : "", false,
                            active ? "aec_suppress_playback" : "no_local_playback");
                }
                applyPolicy(); // Resume phone capture when a PC lease expires offline.
                try {
                    Thread.sleep(1500L);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
        }, "MirrowAmbientPolicy");
        policyThread.setDaemon(false);
        policyThread.start();
    }

    private static boolean validTime(String value) {
        return value != null && value.matches("(?:[01]\\d|2[0-3]):[0-5]\\d");
    }

    private static int minutes(String value) {
        return Integer.parseInt(value.substring(0, 2)) * 60 + Integer.parseInt(value.substring(3, 5));
    }

    private static boolean within(int now, int start, int end) {
        return start <= end ? now >= start && now < end : now >= start || now < end;
    }

    private static boolean withinTime(String start, String end) {
        java.util.Calendar c = java.util.Calendar.getInstance();
        int now = c.get(java.util.Calendar.HOUR_OF_DAY) * 60 + c.get(java.util.Calendar.MINUTE);
        return within(now, minutes(start), minutes(end));
    }

    private static boolean inPrivacyWindow(JSONArray windows) {
        if (windows == null) return false;
        java.util.Calendar c = java.util.Calendar.getInstance();
        int now = c.get(java.util.Calendar.HOUR_OF_DAY) * 60 + c.get(java.util.Calendar.MINUTE);
        for (int i = 0; i < windows.length(); i++) {
            JSONObject item = windows.optJSONObject(i);
            if (item == null || !item.optBoolean("enabled", true)) continue;
            String start = item.optString("start", ""), end = item.optString("end", "");
            if (validTime(start) && validTime(end) && within(now, minutes(start), minutes(end))) return true;
        }
        return false;
    }

    private synchronized void startRecording() {
        if (running.get()) return;
        if (!foregroundPromoter.promote(true)) {
            checkpoint("foreground_service", "fault", "microphone_fgs_upgrade_failed",
                    false, "open_app_and_retry");
            return;
        }
        int min = AudioRecord.getMinBufferSize(SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        try {
            audioRecord = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, Math.max(min * 2, FRAME_BYTES * 8));
            if (audioRecord.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new IllegalStateException("audio_record_uninitialized");
            }
            enableSpeechEffects(audioRecord.getAudioSessionId());
            audioRecord.startRecording();
            running.set(true);
            recordThread = new Thread(this::recordLoop, "MirrowAmbientAudio");
            recordThread.setDaemon(false);
            recordThread.start();
            checkpoint("capture", "success", "", false, "listening");
        } catch (Exception e) {
            running.set(false);
            releaseRecorder();
            checkpoint("capture", "fault", e.getClass().getSimpleName(), true,
                    "check_mic_contention_or_permission");
        }
    }

    private synchronized void stopRecording(String reason) {
        if (!running.getAndSet(false)) return;
        try { if (audioRecord != null) audioRecord.stop(); } catch (Exception ignored) {}
        if (recordThread != null) recordThread.interrupt();
        releaseRecorder();
        // Keep the microphone FGS type while this process remains explicitly
        // armed. Policy transitions may happen with the Activity backgrounded;
        // reacquiring the type at that point would violate Android's while-in-use rule.
        foregroundPromoter.promote(armed);
        checkpoint("capture", "blocked", reason, false, "wait_for_policy");
    }

    private void releaseRecorder() {
        try { if (echoCanceler != null) echoCanceler.release(); } catch (Exception ignored) {}
        try { if (noiseSuppressor != null) noiseSuppressor.release(); } catch (Exception ignored) {}
        echoCanceler = null;
        noiseSuppressor = null;
        playbackSuppressionEnabled = false;
        try { if (audioRecord != null) audioRecord.release(); } catch (Exception ignored) {}
        audioRecord = null;
    }

    private void enableSpeechEffects(int audioSessionId) {
        boolean aecEnabled = false;
        try {
            if (AcousticEchoCanceler.isAvailable()) {
                echoCanceler = AcousticEchoCanceler.create(audioSessionId);
                if (echoCanceler != null) {
                    echoCanceler.setEnabled(true);
                    aecEnabled = echoCanceler.getEnabled();
                }
            }
        } catch (Exception ignored) {
            echoCanceler = null;
        }
        checkpoint("playback_suppression", aecEnabled ? "success" : "partial",
                aecEnabled ? "" : "aec_unavailable", false,
                aecEnabled ? "capture_with_aec" : "inspect_music_leakage");
        playbackSuppressionEnabled = aecEnabled;

        try {
            if (NoiseSuppressor.isAvailable()) {
                noiseSuppressor = NoiseSuppressor.create(audioSessionId);
                if (noiseSuppressor != null) noiseSuppressor.setEnabled(true);
            }
        } catch (Exception ignored) {
            noiseSuppressor = null;
        }
    }

    private void recordLoop() {
        byte[] frame = new byte[FRAME_BYTES];
        ArrayDeque<byte[]> preRoll = new ArrayDeque<>();
        ByteArrayOutputStream segment = null;
        JSONObject segmentContext = null;
        int speechFrames = 0, silenceFrames = 0, totalFrames = 0;
        long segmentStarted = 0L;
        while (running.get()) {
            AudioRecord recorder = audioRecord;
            if (recorder == null) break;
            int read = recorder.read(frame, 0, frame.length);
            if (read <= 0) {
                if (!running.get()) break;
                checkpoint("capture_read", "fault", "audio_read_" + read, true,
                        "check_mic_contention_or_route");
                break;
            }
            byte[] copy = Arrays.copyOf(frame, read);
            boolean speech = energy(copy) >= SPEECH_THRESHOLD;
            if (segment == null) {
                preRoll.addLast(copy);
                while (preRoll.size() > 15) preRoll.removeFirst();
                speechFrames = speech ? speechFrames + 1 : 0;
                if (speechFrames >= START_FRAMES) {
                    segment = new ByteArrayOutputStream();
                    for (byte[] old : preRoll) segment.write(old, 0, old.length);
                    totalFrames = preRoll.size();
                    silenceFrames = 0;
                    segmentStarted = System.currentTimeMillis();
                    segmentContext = contextProvider == null
                            ? new JSONObject() : contextProvider.snapshot();
                    preRoll.clear();
                }
                continue;
            }
            segment.write(copy, 0, copy.length);
            totalFrames++;
            silenceFrames = speech ? 0 : silenceFrames + 1;
            if ((silenceFrames >= SILENCE_FRAMES && totalFrames >= MIN_SEGMENT_FRAMES)
                    || totalFrames >= MAX_SEGMENT_FRAMES) {
                persistSegment(segment.toByteArray(), segmentStarted, segmentContext);
                segment = null;
                segmentContext = null;
                speechFrames = silenceFrames = totalFrames = 0;
            }
        }
        boolean unexpected = running.getAndSet(false);
        if (unexpected) {
            checkpoint("capture", "fault", "capture_loop_stopped", true,
                    "open_app_if_permission_or_route_changed");
        }
    }

    private static int energy(byte[] pcm) {
        long sum = 0; int count = 0;
        ByteBuffer buffer = ByteBuffer.wrap(pcm).order(ByteOrder.LITTLE_ENDIAN);
        while (buffer.remaining() >= 2) { sum += Math.abs((int) buffer.getShort()); count++; }
        return count == 0 ? 0 : (int) (sum / count);
    }

    private synchronized void persistSegment(byte[] pcm, long capturedAt, JSONObject audioContext) {
        String clipId = UUID.randomUUID().toString();
        File part = new File(cacheDir, "ambient_" + capturedAt + "_" + clipId + ".wav.part");
        File target = new File(cacheDir, "ambient_" + capturedAt + "_" + clipId + ".wav");
        try (FileOutputStream out = new FileOutputStream(part)) {
            out.write(wavHeader(pcm.length));
            out.write(pcm);
            out.getFD().sync();
            if (!part.renameTo(target)) throw new IllegalStateException("cache_rename_failed");
            writeContextSidecar(target, audioContext);
            checkpoint("segment_cache", "success", "", false, "upload_when_connected");
            flushOne();
        } catch (Exception e) {
            part.delete();
            checkpoint("segment_cache", "fault", e.getClass().getSimpleName(), false,
                    "check_private_storage");
        }
    }

    private void writeContextSidecar(File audio, JSONObject audioContext) {
        File target = new File(audio.getAbsolutePath() + ".json");
        File part = new File(target.getAbsolutePath() + ".part");
        try (FileOutputStream out = new FileOutputStream(part)) {
            byte[] bytes = (audioContext == null ? "{}" : audioContext.toString())
                    .getBytes(java.nio.charset.StandardCharsets.UTF_8);
            out.write(bytes);
            out.getFD().sync();
            if (!part.renameTo(target)) throw new IllegalStateException("context_rename_failed");
        } catch (Exception e) {
            part.delete();
            checkpoint("segment_context", "partial", e.getClass().getSimpleName(), false,
                    "upload_with_unknown_context");
        }
    }

    private static byte[] wavHeader(int dataLength) {
        ByteBuffer h = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN);
        h.put(new byte[]{'R','I','F','F'}).putInt(36 + dataLength)
                .put(new byte[]{'W','A','V','E','f','m','t',' '}).putInt(16).putShort((short) 1)
                .putShort((short) 1).putInt(SAMPLE_RATE).putInt(SAMPLE_RATE * 2)
                .putShort((short) 2).putShort((short) 16)
                .put(new byte[]{'d','a','t','a'}).putInt(dataLength);
        return h.array();
    }

    synchronized void flushOne() {
        if (!inFlightClip.isEmpty()) return;
        cleanupExpired();
        File[] files = cacheDir.listFiles((dir, name) -> name.endsWith(".wav"));
        if (files == null || files.length == 0) return;
        Arrays.sort(files, (a, b) -> Long.compare(a.lastModified(), b.lastModified()));
        File file = files[0];
        String[] parts = file.getName().replace(".wav", "").split("_", 3);
        if (parts.length != 3) { file.delete(); return; }
        String clipId = parts[2];
        try {
            byte[] bytes = readAll(file);
            JSONObject msg = new JSONObject();
            msg.put("type", "ambient_audio_segment");
            msg.put("clip_id", clipId);
            msg.put("correlation_id", clipId);
            msg.put("captured_at_ms", Long.parseLong(parts[1]));
            msg.put("audio_b64", Base64.encodeToString(bytes, Base64.NO_WRAP));
            File contextFile = new File(file.getAbsolutePath() + ".json");
            if (contextFile.isFile()) {
                try {
                    msg.put("audio_context", new JSONObject(
                            new String(readAll(contextFile), java.nio.charset.StandardCharsets.UTF_8)));
                } catch (Exception e) {
                    checkpoint("segment_context", "partial", "context_read_failed", false,
                            "upload_with_unknown_context");
                }
            }
            if (sender.send(msg)) {
                inFlightClip = clipId;
                checkpoint("upload", "success", "", true, "wait_for_host_receipt");
            }
        } catch (Exception e) {
            checkpoint("upload", "fault", e.getClass().getSimpleName(), true,
                    "retry_after_reconnect");
        }
    }

    private static byte[] readAll(File file) throws Exception {
        try (FileInputStream input = new FileInputStream(file);
             ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buf = new byte[16 * 1024]; int n;
            while ((n = input.read(buf)) > 0) out.write(buf, 0, n);
            return out.toByteArray();
        }
    }

    private synchronized void cleanupExpired() {
        long cutoff = System.currentTimeMillis() - MAX_LOCAL_AGE_MS;
        File[] files = cacheDir.listFiles();
        if (files == null) return;
        for (File file : files) {
            long captured = capturedAt(file);
            if (captured <= 0 || captured < cutoff) file.delete();
        }
    }

    private static long capturedAt(File file) {
        try { return Long.parseLong(file.getName().split("_", 3)[1]); }
        catch (Exception e) { return 0L; }
    }

    private File findClip(String clipId) {
        File[] files = cacheDir.listFiles((dir, name) -> name.endsWith("_" + clipId + ".wav"));
        return files != null && files.length > 0 ? files[0] : null;
    }

    private void checkpoint(String stage, String status, String errorCode,
                            boolean retryable, String nextAction) {
        try {
            JSONObject msg = new JSONObject();
            msg.put("type", "ambient_checkpoint");
            msg.put("correlation_id", inFlightClip.isEmpty() ? "mobile-runtime" : inFlightClip);
            msg.put("stage", stage);
            msg.put("status", status);
            msg.put("error_code", errorCode == null ? "" : errorCode);
            msg.put("retryable", retryable);
            msg.put("next_action", nextAction == null ? "" : nextAction);
            sender.send(msg);
        } catch (Exception ignored) {}
    }
}
