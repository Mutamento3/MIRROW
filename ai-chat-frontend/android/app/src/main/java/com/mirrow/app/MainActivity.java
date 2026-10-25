package com.mirrow.app;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.getcapacitor.BridgeActivity;
import com.mirrow.app.relay.RelayObservabilityStore;
import com.mirrow.app.relay.RelayWatchdogController;

import org.json.JSONObject;

public class MainActivity extends BridgeActivity {
    @Override public void onResume() {
        super.onResume();
        com.mirrow.app.miband.MiBandLocalImport.consume(this);
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        com.mirrow.app.miband.MiBandLocalImport.consume(this);
    }
    private static final int REQUEST_AUDIO = 100;
    private static final int REQUEST_LEGACY_IMAGE_STORAGE = 101;
    private static final int REQUEST_BACKGROUND_CAMERA = 102;
    private static final String PREF_BACKGROUND_CAMERA_PERMISSION_ASKED =
            "background_camera_permission_asked";
    private CameraFileChooserController fileChooserController;
    private boolean audioPermissionRequestInFlight = false;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        this.initialPlugins.add(com.mirrow.app.plugins.MobileToolPlugin.class);
        this.initialPlugins.add(com.mirrow.app.plugins.MiBandPlugin.class);
        super.onCreate(savedInstanceState);
        fileChooserController = new CameraFileChooserController(this);

        // 启动原生 relay Service（前台保活，替代 Capacitor WebView WebSocket）
        Intent relayIntent = new Intent(this, MirrowRelayService.class);
        relayIntent.setPackage(getPackageName());
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(relayIntent);
            } else {
                startService(relayIntent);
            }
        } catch (Exception e) {
            android.util.Log.w("MainActivity", "MirrowRelayService 启动失败: " + e.getMessage());
        }
    }

    @Override
    public void onStart() {
        super.onStart();
        RelayWatchdogController.triggerRepair(this, "app_foreground");
        RelayObservabilityStore.record(this, "mirrow_foreground", new JSONObject());

        WebView webView = this.bridge.getWebView();
        if (webView != null) {
            // MIRROW 自己的 Virtuoso 区域负责滚动。关闭 WebView 外层的滚动条与
            // overscroll 边缘效果，避免左沿在普通/长截图中被绘制成深色阴影。
            webView.setVerticalScrollBarEnabled(false);
            webView.setHorizontalScrollBarEnabled(false);
            webView.setOverScrollMode(android.view.View.OVER_SCROLL_NEVER);
            webView.setBackgroundColor(android.graphics.Color.rgb(255, 254, 245));

            WebSettings settings = webView.getSettings();
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
            if (android.os.Build.VERSION.SDK_INT >= 26) {
                settings.setSafeBrowsingEnabled(false);
            }
            // 启用 WebView 的 getUserMedia 支持（Android 15+ ColorOS 可能需要显式开启）
            settings.setMediaPlaybackRequiresUserGesture(false);

            // 设置 WebChromeClient 处理麦克风权限请求 + 文件选择（onShowFileChooser）
            webView.setWebChromeClient(new WebChromeClient() {
                @Override
                public void onPermissionRequest(PermissionRequest request) {
                    String[] resources = request.getResources();
                    for (String r : resources) {
                        android.util.Log.i("MainActivity", "WebView permission: " + r);
                    }
                    request.grant(resources);
                }

                @Override
                public boolean onShowFileChooser(WebView webView,
                        android.webkit.ValueCallback<android.net.Uri[]> filePathCallback,
                        FileChooserParams fileChooserParams) {
                    return fileChooserController.show(filePathCallback, fileChooserParams);
                }
            });

            // 原生麦克风桥接（绕过 getUserMedia 限制）
            AudioBridge audioBridge = new AudioBridge(webView);
            webView.addJavascriptInterface(audioBridge, "MirrowAudio");
            android.util.Log.i("MainActivity", "AudioBridge registered as window.MirrowAudio");
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            // Android permits only one runtime permission dialog at a time. Do not mark or
            // request the background camera until this existing audio request has resolved.
            if (!audioPermissionRequestInFlight) {
                audioPermissionRequestInFlight = true;
                ActivityCompat.requestPermissions(
                        this,
                        new String[]{Manifest.permission.RECORD_AUDIO},
                        REQUEST_AUDIO);
            }
            return;
        }
        armAmbientMicrophoneService();
        continueBackgroundCameraPermissionSetup();
    }

    private void continueBackgroundCameraPermissionSetup() {
        // Android 10+ 通过 MediaStore scoped storage 保存图片无需权限；仅兼容
        // 项目仍支持的 Android 7–9，且权限在 manifest 中已限制 maxSdkVersion=28。
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P
                && ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(
                    this,
                    new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE},
                    REQUEST_LEGACY_IMAGE_STORAGE);
            return;
        }

        // This runs only while the Activity is visible. It is the legal handoff point for
        // Android 14+'s while-in-use camera permission: the relay never cold-starts a camera
        // foreground service from the background or after boot.
        armBackgroundCameraIfAllowed();
    }

    @Override
    public void onStop() {
        RelayObservabilityStore.record(this, "mirrow_background", new JSONObject());
        super.onStop();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (fileChooserController != null
                && fileChooserController.handleActivityResult(requestCode, resultCode, data)) return;
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions,
                                           int[] grantResults) {
        if (fileChooserController != null
                && fileChooserController.handlePermissionResult(requestCode, grantResults)) return;
        if (requestCode == REQUEST_AUDIO) {
            audioPermissionRequestInFlight = false;
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                armAmbientMicrophoneService();
            }
            continueBackgroundCameraPermissionSetup();
            return;
        }
        if (requestCode == REQUEST_LEGACY_IMAGE_STORAGE) {
            continueBackgroundCameraPermissionSetup();
            return;
        }
        if (requestCode == REQUEST_BACKGROUND_CAMERA) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                armBackgroundCameraService();
            }
            return;
        }
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
    }

    private void armBackgroundCameraIfAllowed() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED) {
            armBackgroundCameraService();
            return;
        }
        // A denied request is not retried on every foreground transition. The user can grant
        // it later in system settings, then the next visible start arms normally.
        android.content.SharedPreferences prefs = getSharedPreferences(
                com.mirrow.app.network.BackendEndpointPool.PREFS_NAME, MODE_PRIVATE);
        if (!prefs.getBoolean(PREF_BACKGROUND_CAMERA_PERMISSION_ASKED, false)) {
            try {
                ActivityCompat.requestPermissions(this, new String[]{Manifest.permission.CAMERA},
                        REQUEST_BACKGROUND_CAMERA);
                // Mark only after the request has actually been submitted. If the call cannot
                // start a dialog, a later visible launch remains eligible to ask once.
                prefs.edit().putBoolean(PREF_BACKGROUND_CAMERA_PERMISSION_ASKED, true).apply();
            } catch (Exception e) {
                android.util.Log.w("MainActivity", "后台相机权限请求未发起: " + e.getMessage());
            }
        }
    }

    private void armBackgroundCameraService() {
        Intent intent = new Intent(this, MirrowRelayService.class)
                .setAction(MirrowRelayService.ACTION_ARM_BACKGROUND_CAMERA)
                .setPackage(getPackageName());
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(intent);
            else startService(intent);
        } catch (Exception e) {
            android.util.Log.w("MainActivity", "后台相机 arm 启动失败: " + e.getMessage());
        }
    }

    private void armAmbientMicrophoneService() {
        Intent intent = new Intent(this, MirrowRelayService.class)
                .setAction(MirrowRelayService.ACTION_ARM_AMBIENT_MICROPHONE)
                .setPackage(getPackageName());
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(intent);
            else startService(intent);
        } catch (Exception e) {
            android.util.Log.w("MainActivity", "环境监听麦克风 arm 启动失败: " + e.getMessage());
        }
    }
}
