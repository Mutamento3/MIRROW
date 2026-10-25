package com.mirrow.app;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.util.Base64;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

/**
 * 原生麦克风桥接 — 绕过 WebView getUserMedia 的限制
 * 按住录音：JS 调 start() → 实时推 PCM Base64 帧 → JS 调 stop()
 *
 * JS 端: window.MirrowAudio.start() / stop() / isRecording()
 * JS 接收: window._mirrowAudioChunk(base64Pcm)
 */
public class AudioBridge {

    private static final int SAMPLE_RATE = 16000;
    private static final int CHANNEL = AudioFormat.CHANNEL_IN_MONO;
    private static final int ENCODING = AudioFormat.ENCODING_PCM_16BIT;
    private static final int FRAME_BYTES = 1280; // 40ms @ 16kHz 16bit mono

    private final WebView webView;
    private AudioRecord recorder;
    private volatile boolean recording = false;

    public AudioBridge(WebView webView) {
        this.webView = webView;
    }

    @JavascriptInterface
    public boolean start() {
        if (recording) return false;

        int minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL, ENCODING);
        int bufSize = Math.max(minBuf, FRAME_BYTES * 4);

        try {
            recorder = new AudioRecord(
                    MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    SAMPLE_RATE, CHANNEL, ENCODING, bufSize);
        } catch (SecurityException e) {
            android.util.Log.e("AudioBridge", "RECORD_AUDIO permission denied");
            return false;
        }

        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            recorder.release();
            recorder = null;
            return false;
        }

        recorder.startRecording();
        recording = true;

        new Thread(() -> {
            byte[] buf = new byte[FRAME_BYTES];
            while (recording) {
                int read = recorder.read(buf, 0, buf.length);
                if (read > 0) {
                    String b64 = Base64.encodeToString(buf, 0, read, Base64.NO_WRAP);
                    webView.post(() -> {
                        if (recording) {
                            webView.evaluateJavascript(
                                "window._mirrowAudioChunk&&window._mirrowAudioChunk('" + b64 + "')",
                                null);
                        }
                    });
                }
            }
        }, "MirrowMicThread").start();

        return true;
    }

    @JavascriptInterface
    public void stop() {
        recording = false;
        if (recorder != null) {
            try { recorder.stop(); } catch (Exception ignored) {}
            try { recorder.release(); } catch (Exception ignored) {}
            recorder = null;
        }
    }

    @JavascriptInterface
    public boolean isRecording() {
        return recording;
    }
}
