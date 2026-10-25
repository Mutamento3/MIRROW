package com.mirrow.app;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;
import android.util.Size;

import androidx.annotation.NonNull;
import androidx.core.content.ContextCompat;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.Collections;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Native Camera2 one-shot capture for {@link MirrowRelayService}.
 *
 * Based on the architecture (not service code) of AionsHome's MIT-licensed
 * PhoneCameraController.  It has no Activity, LifecycleOwner or WebView
 * dependency, and opens the camera only for an armed relay request.
 */
final class BackgroundCameraController {
    private static final String TAG = "MirrowBgCamera";
    private static final long CAPTURE_TIMEOUT_MS = 10_000L;

    interface Callback {
        void onSuccess(byte[] jpeg, JSONObject metadata);
        void onFailure(String error);
    }

    private final Context context;
    private final CameraManager cameraManager;
    private final Object lock = new Object();
    private final BackgroundCameraCaptureGate captureGate = new BackgroundCameraCaptureGate();
    private HandlerThread thread;
    private Handler handler;
    private CameraDevice device;
    private CameraCaptureSession session;
    private ImageReader reader;
    private ActiveCapture activeCapture;

    BackgroundCameraController(Context context) {
        this.context = context.getApplicationContext();
        this.cameraManager = (CameraManager) this.context.getSystemService(Context.CAMERA_SERVICE);
    }

    boolean isAvailable() {
        return cameraManager != null;
    }

    boolean capture(String facing, Callback callback) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            callback.onFailure("camera_permission_denied");
            return false;
        }
        final ActiveCapture capture;
        synchronized (lock) {
            long token = captureGate.begin();
            if (token < 0L) {
                callback.onFailure("camera_busy");
                return false;
            }
            capture = new ActiveCapture(token, callback);
            activeCapture = capture;
            ensureThread();
            if (handler == null || !handler.post(() -> openAndCapture(
                    BackgroundCameraImagePolicy.normalizeFacing(facing), capture))) {
                captureGate.complete(capture.token);
                activeCapture = null;
                callback.onFailure("camera_thread_unavailable");
                return false;
            }
        }
        return true;
    }

    private void openAndCapture(String facing, ActiveCapture capture) {
        if (!isCurrent(capture)) return;
        try {
            CameraChoice choice = chooseCamera(facing);
            if (choice == null) {
                finishFailure(capture, "camera_not_found");
                return;
            }
            ImageReader localReader = ImageReader.newInstance(
                    choice.size.getWidth(), choice.size.getHeight(), android.graphics.ImageFormat.JPEG, 2);
            synchronized (lock) {
                if (!isCurrentLocked(capture)) { localReader.close(); return; }
                reader = localReader;
            }
            localReader.setOnImageAvailableListener(imageReader -> {
                Image image = null;
                try {
                    image = imageReader.acquireLatestImage();
                    if (image == null || !isCurrent(capture)) return;
                    ByteBuffer buffer = image.getPlanes()[0].getBuffer();
                    byte[] raw = new byte[buffer.remaining()];
                    buffer.get(raw);
                    byte[] jpeg = normalizeAndCompress(raw);
                    JSONObject metadata = new JSONObject();
                    metadata.put("source", "native_background_camera");
                    metadata.put("facing", facing);
                    metadata.put("width", choice.size.getWidth());
                    metadata.put("height", choice.size.getHeight());
                    metadata.put("byte_size", jpeg.length);
                    finishSuccess(capture, jpeg, metadata);
                } catch (Exception e) {
                    finishFailure(capture, "image_processing_failed");
                } finally {
                    if (image != null) image.close();
                }
            }, handler);
            handler.postDelayed(() -> finishFailure(capture, "capture_timeout"), CAPTURE_TIMEOUT_MS);
            cameraManager.openCamera(choice.id, new CameraDevice.StateCallback() {
                @Override public void onOpened(@NonNull CameraDevice camera) {
                    synchronized (lock) {
                        if (!isCurrentLocked(capture)) { camera.close(); return; }
                        device = camera;
                    }
                    createSession(camera, localReader, capture);
                }
                @Override public void onDisconnected(@NonNull CameraDevice camera) {
                    camera.close(); finishFailure(capture, "camera_disconnected");
                }
                @Override public void onError(@NonNull CameraDevice camera, int error) {
                    camera.close(); finishFailure(capture, "camera_error_" + error);
                }
            }, handler);
        } catch (SecurityException e) {
            finishFailure(capture, "camera_permission_denied");
        } catch (CameraAccessException e) {
            finishFailure(capture, cameraAccessError(e));
        } catch (Exception e) {
            Log.w(TAG, "camera setup failed", e);
            finishFailure(capture, "camera_setup_failed");
        }
    }

    private void createSession(CameraDevice camera, ImageReader localReader,
                               ActiveCapture capture) {
        try {
            camera.createCaptureSession(Collections.singletonList(localReader.getSurface()),
                    new CameraCaptureSession.StateCallback() {
                        @Override public void onConfigured(@NonNull CameraCaptureSession configured) {
                            if (!isCurrent(capture)) { configured.close(); return; }
                            synchronized (lock) {
                                if (!isCurrentLocked(capture)) { configured.close(); return; }
                                session = configured;
                            }
                            try {
                                CaptureRequest.Builder request = camera.createCaptureRequest(
                                        CameraDevice.TEMPLATE_STILL_CAPTURE);
                                request.addTarget(localReader.getSurface());
                                request.set(CaptureRequest.CONTROL_AF_MODE,
                                        CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
                                request.set(CaptureRequest.CONTROL_AE_MODE,
                                        CaptureRequest.CONTROL_AE_MODE_ON);
                                request.set(CaptureRequest.JPEG_QUALITY, (byte) 90);
                                configured.capture(request.build(),
                                        new CameraCaptureSession.CaptureCallback() {}, handler);
                            } catch (CameraAccessException e) {
                                finishFailure(capture, cameraAccessError(e));
                            } catch (Exception e) {
                                finishFailure(capture, "still_capture_failed");
                            }
                        }
                        @Override public void onConfigureFailed(@NonNull CameraCaptureSession failed) {
                            finishFailure(capture, "capture_session_configuration_failed");
                        }
                    }, handler);
        } catch (CameraAccessException e) {
            finishFailure(capture, cameraAccessError(e));
        }
    }

    private CameraChoice chooseCamera(String facing) throws CameraAccessException {
        int wanted = "front".equals(facing) ? CameraCharacteristics.LENS_FACING_FRONT
                : CameraCharacteristics.LENS_FACING_BACK;
        for (String id : cameraManager.getCameraIdList()) {
            CameraCharacteristics chars = cameraManager.getCameraCharacteristics(id);
            Integer actual = chars.get(CameraCharacteristics.LENS_FACING);
            if (actual == null || actual != wanted) continue;
            android.hardware.camera2.params.StreamConfigurationMap map = chars.get(
                    CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            Size[] sizes = map == null ? null : map.getOutputSizes(android.graphics.ImageFormat.JPEG);
            if (sizes == null || sizes.length == 0) continue;
            Size chosen = Arrays.stream(sizes).min((a, b) -> Integer.compare(
                    Math.abs(Math.max(a.getWidth(), a.getHeight()) - BackgroundCameraImagePolicy.MAX_LONG_EDGE),
                    Math.abs(Math.max(b.getWidth(), b.getHeight()) - BackgroundCameraImagePolicy.MAX_LONG_EDGE)))
                    .orElse(sizes[0]);
            return new CameraChoice(id, chosen);
        }
        return null;
    }

    private static byte[] normalizeAndCompress(byte[] raw) {
        Bitmap source = BitmapFactory.decodeByteArray(raw, 0, raw.length);
        if (source == null) throw new IllegalArgumentException("decode_failed");
        int[] target = BackgroundCameraImagePolicy.targetDimensions(source.getWidth(), source.getHeight());
        Bitmap scaled = source;
        if (target[0] != source.getWidth() || target[1] != source.getHeight()) {
            scaled = Bitmap.createScaledBitmap(source, target[0], target[1], true);
            source.recycle();
        }
        int quality = BackgroundCameraImagePolicy.INITIAL_JPEG_QUALITY;
        byte[] encoded;
        do {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            scaled.compress(Bitmap.CompressFormat.JPEG, quality, output);
            encoded = output.toByteArray();
            if (encoded.length <= BackgroundCameraImagePolicy.MAX_JPEG_BYTES
                    || quality <= BackgroundCameraImagePolicy.MIN_JPEG_QUALITY) break;
            quality = BackgroundCameraImagePolicy.nextQuality(quality);
        } while (true);
        scaled.recycle();
        return encoded;
    }

    private void finishSuccess(ActiveCapture capture, byte[] jpeg, JSONObject metadata) {
        Callback callback = finishCurrent(capture);
        if (callback != null) callback.onSuccess(jpeg, metadata);
    }

    private void finishFailure(ActiveCapture capture, String error) {
        Callback callback = finishCurrent(capture);
        if (callback != null) callback.onFailure(error);
    }

    /** Returns the callback exactly once, only while this token owns the shared resources. */
    private Callback finishCurrent(ActiveCapture capture) {
        synchronized (lock) {
            if (!isCurrentLocked(capture) || !capture.completed.compareAndSet(false, true)) return null;
            captureGate.complete(capture.token);
            activeCapture = null;
            closeResourcesLocked();
            return capture.callback;
        }
    }

    void close() {
        Callback cancelledCallback = null;
        synchronized (lock) {
            ActiveCapture cancelled = activeCapture;
            captureGate.cancel();
            activeCapture = null;
            closeResourcesLocked();
            if (cancelled != null && cancelled.completed.compareAndSet(false, true)) {
                cancelledCallback = cancelled.callback;
            }
            if (thread != null) { thread.quitSafely(); thread = null; handler = null; }
        }
        // The server receives a concrete cancellation instead of waiting for its relay timeout.
        if (cancelledCallback != null) cancelledCallback.onFailure("camera_cancelled");
    }

    private boolean isCurrent(ActiveCapture capture) {
        synchronized (lock) { return isCurrentLocked(capture); }
    }

    private boolean isCurrentLocked(ActiveCapture capture) {
        return capture != null && activeCapture == capture && !capture.completed.get()
                && captureGate.isCurrent(capture.token);
    }

    private void closeResourcesLocked() {
        if (session != null) { try { session.close(); } catch (Exception ignored) {} session = null; }
        if (device != null) { try { device.close(); } catch (Exception ignored) {} device = null; }
        if (reader != null) { try { reader.close(); } catch (Exception ignored) {} reader = null; }
    }

    private void ensureThread() {
        if (thread != null && thread.isAlive()) return;
        thread = new HandlerThread("MirrowBackgroundCamera");
        thread.start();
        handler = new Handler(thread.getLooper());
    }

    private static String cameraAccessError(CameraAccessException error) {
        switch (error.getReason()) {
            case CameraAccessException.CAMERA_IN_USE: return "camera_access_in_use";
            case CameraAccessException.MAX_CAMERAS_IN_USE: return "camera_access_max_in_use";
            case CameraAccessException.CAMERA_DISABLED: return "camera_access_disabled";
            case CameraAccessException.CAMERA_DISCONNECTED: return "camera_access_disconnected";
            default: return "camera_access_error";
        }
    }

    private static final class CameraChoice {
        final String id;
        final Size size;
        CameraChoice(String id, Size size) { this.id = id; this.size = size; }
    }

    private static final class ActiveCapture {
        final long token;
        final Callback callback;
        final AtomicBoolean completed = new AtomicBoolean(false);
        ActiveCapture(long token, Callback callback) {
            this.token = token;
            this.callback = callback;
        }
    }
}
