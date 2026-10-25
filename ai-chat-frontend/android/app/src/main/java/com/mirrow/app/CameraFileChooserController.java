package com.mirrow.app;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Log;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;

import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import java.util.LinkedHashSet;
import java.util.Set;

/**
 * WebView 文件选择边界：普通 input 走系统文件选择器，capture=image 直接走后摄。
 */
final class CameraFileChooserController {
    static final int FILE_CHOOSER_REQUEST_CODE = 1001;
    static final int CAMERA_PERMISSION_REQUEST_CODE = 1002;
    private static final String TAG = "CameraFileChooser";

    private final Activity activity;
    private ValueCallback<Uri[]> callback;
    private Uri cameraOutputUri;
    private boolean waitingForCameraPermission;

    CameraFileChooserController(Activity activity) {
        this.activity = activity;
    }

    boolean show(ValueCallback<Uri[]> newCallback,
                 WebChromeClient.FileChooserParams params) {
        cancelPending();
        callback = newCallback;

        boolean captureImage = params.isCaptureEnabled() && acceptsImages(params.getAcceptTypes());
        Log.i(TAG, "show capture=" + params.isCaptureEnabled()
                + " accept=" + String.join(",", params.getAcceptTypes())
                + " route=" + (captureImage ? "camera" : "picker"));
        if (captureImage) {
            return ensurePermissionAndLaunchCamera();
        }
        return launchPicker(params);
    }

    private static boolean acceptsImages(String[] acceptTypes) {
        if (acceptTypes == null || acceptTypes.length == 0) return false;
        for (String type : acceptTypes) {
            if (type != null && type.toLowerCase().startsWith("image/")) return true;
        }
        return false;
    }

    private boolean ensurePermissionAndLaunchCamera() {
        if (ContextCompat.checkSelfPermission(activity, Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            waitingForCameraPermission = true;
            ActivityCompat.requestPermissions(
                    activity,
                    new String[]{Manifest.permission.CAMERA},
                    CAMERA_PERMISSION_REQUEST_CODE
            );
            return true;
        }
        return launchCamera();
    }

    private boolean launchCamera() {
        waitingForCameraPermission = false;
        ContentValues values = new ContentValues();
        values.put(MediaStore.Images.Media.DISPLAY_NAME,
                "MIRROW_" + System.currentTimeMillis() + ".jpg");
        values.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            values.put(MediaStore.Images.Media.RELATIVE_PATH,
                    Environment.DIRECTORY_PICTURES + "/MIRROW");
        }
        cameraOutputUri = activity.getContentResolver().insert(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                values
        );
        if (cameraOutputUri == null) {
            Log.w(TAG, "无法创建相机输出 URI");
            finish(null, false);
            return false;
        }

        Intent cameraIntent = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
        cameraIntent.putExtra(MediaStore.EXTRA_OUTPUT, cameraOutputUri);
        cameraIntent.setClipData(ClipData.newRawUri("MIRROW camera output", cameraOutputUri));
        cameraIntent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                | Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            activity.startActivityForResult(cameraIntent, FILE_CHOOSER_REQUEST_CODE);
            return true;
        } catch (Exception e) {
            Log.w(TAG, "启动系统相机失败: " + e.getMessage());
            finish(null, true);
            return false;
        }
    }

    private boolean launchPicker(WebChromeClient.FileChooserParams params) {
        Intent intent = params.createIntent();
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        intent.putExtra(
                Intent.EXTRA_ALLOW_MULTIPLE,
                params.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE
        );
        try {
            activity.startActivityForResult(
                    Intent.createChooser(intent, "选择文件"),
                    FILE_CHOOSER_REQUEST_CODE
            );
            return true;
        } catch (Exception e) {
            Log.w(TAG, "启动文件选择器失败: " + e.getMessage());
            finish(null, false);
            return false;
        }
    }

    boolean handleActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != FILE_CHOOSER_REQUEST_CODE) return false;
        Uri[] results = null;
        if (resultCode == Activity.RESULT_OK) {
            if (cameraOutputUri != null) {
                results = new Uri[]{cameraOutputUri};
            } else {
                results = extractPickerUris(data);
            }
        }
        Log.i(TAG, "result ok=" + (resultCode == Activity.RESULT_OK)
                + " source=" + (cameraOutputUri != null ? "camera" : "picker")
                + " uriCount=" + (results == null ? 0 : results.length));
        finish(results, resultCode != Activity.RESULT_OK);
        return true;
    }

    /**
     * ColorOS 的系统相册多选通过 ClipData 返回；WebView 的 parseResult 在部分
     * 系统版本只读取 data Uri，会把已经确认的多选结果解析成 null。
     */
    private static Uri[] extractPickerUris(Intent data) {
        if (data == null) return null;
        Set<Uri> uris = new LinkedHashSet<>();

        ClipData clipData = data.getClipData();
        if (clipData != null) {
            for (int i = 0; i < clipData.getItemCount(); i++) {
                Uri uri = clipData.getItemAt(i).getUri();
                if (uri != null) uris.add(uri);
            }
        }

        Uri singleUri = data.getData();
        if (singleUri != null) uris.add(singleUri);

        if (uris.isEmpty()) {
            Uri[] parsed = WebChromeClient.FileChooserParams.parseResult(
                    Activity.RESULT_OK,
                    data
            );
            if (parsed != null) {
                for (Uri uri : parsed) {
                    if (uri != null) uris.add(uri);
                }
            }
        }
        return uris.isEmpty() ? null : uris.toArray(new Uri[0]);
    }

    boolean handlePermissionResult(int requestCode, int[] grantResults) {
        if (requestCode != CAMERA_PERMISSION_REQUEST_CODE || !waitingForCameraPermission) {
            return false;
        }
        boolean granted = grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED;
        if (granted) {
            launchCamera();
        } else {
            finish(null, false);
        }
        return true;
    }

    private void cancelPending() {
        if (callback != null) callback.onReceiveValue(null);
        callback = null;
        deleteUnusedCameraOutput();
        waitingForCameraPermission = false;
    }

    private void finish(Uri[] results, boolean deleteCameraOutput) {
        if (callback != null) callback.onReceiveValue(results);
        callback = null;
        if (deleteCameraOutput) deleteUnusedCameraOutput();
        cameraOutputUri = null;
        waitingForCameraPermission = false;
    }

    private void deleteUnusedCameraOutput() {
        if (cameraOutputUri == null) return;
        try {
            activity.getContentResolver().delete(cameraOutputUri, null, null);
        } catch (Exception ignored) {
        }
        cameraOutputUri = null;
    }
}
