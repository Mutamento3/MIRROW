package com.mirrow.app.plugins;

import android.Manifest;
import android.app.AlertDialog;
import android.os.Build;
import android.text.InputType;
import android.widget.EditText;
import com.getcapacitor.*;
import com.getcapacitor.annotation.*;
import com.mirrow.app.miband.*;
import org.json.JSONArray;
import java.util.List;

@CapacitorPlugin(name="MiBand", permissions={
    @Permission(alias="nearby", strings={Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT}),
    @Permission(alias="location", strings={Manifest.permission.ACCESS_FINE_LOCATION})
})
public class MiBandPlugin extends Plugin {
    private MiBandScanner scanner;
    @PluginMethod public void execute(PluginCall call) {
        new Thread(() -> {
            try { call.resolve(JSObject.fromJSONObject(MiBandBridge.execute(getContext(), call.getData()))); }
            catch (Exception e) { call.reject("手环操作失败：" + e.getMessage()); }
        }, "mirrow-band-action").start();
    }
    @PluginMethod public void scan(PluginCall call) {
        String alias = Build.VERSION.SDK_INT >= 31 ? "nearby" : "location";
        if (getPermissionState(alias) != PermissionState.GRANTED) {
            requestPermissionForAlias(alias, call, "afterPermission"); return;
        }
        startScan(call);
    }
    @PermissionCallback private void afterPermission(PluginCall call) {
        String alias = Build.VERSION.SDK_INT >= 31 ? "nearby" : "location";
        if (getPermissionState(alias) != PermissionState.GRANTED) { call.reject("未授权附近设备／定位"); return; }
        startScan(call);
    }
    private void startScan(PluginCall call) {
        if (scanner != null) { call.reject("扫描尚未结束"); return; }
        scanner = new MiBandScanner(getContext(), new MiBandScanner.Listener() {
            private List<MiBandScanResult> latest = java.util.Collections.emptyList();
            @Override public void onScanResults(List<MiBandScanResult> results) { latest = results; }
            @Override public void onScanState(boolean scanning, String message) {
                if (scanning) return;
                JSONArray devices = new JSONArray();
                for (MiBandScanResult item : latest) {
                    JSObject row = new JSObject(); row.put("address", item.address); row.put("name", item.name); devices.put(row);
                }
                JSObject result = new JSObject(); result.put("devices", devices); result.put("message", message);
                scanner = null; call.resolve(result);
            }
        });
        scanner.start();
    }
    @PluginMethod public void configure(PluginCall call) {
        String address = call.getString("address", "");
        String name = call.getString("name", "Xiaomi Smart Band 7");
        if (!address.matches("(?i)^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")) { call.reject("请先扫描选择手环"); return; }
        // Credential never crosses Capacitor JS, relay, backend, clipboard or logs.
        getActivity().runOnUiThread(() -> {
            EditText input = new EditText(getContext());
            input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
            input.setHint(MiBandRuntime.get(getContext()).authKeyConfigured() ? "已保存密钥；留空保留（仅同一设备）" : "32位十六进制 auth_key");
            new AlertDialog.Builder(getActivity()).setTitle("在手机本地配置手环密钥")
                .setMessage("密钥仅保存于本机，不发送给 AI 或服务器。")
                .setView(input).setNegativeButton("取消", (d,w) -> call.reject("已取消"))
                .setPositiveButton("保存", (d,w) -> {
                    try {
                        MiBandRuntime r = MiBandBridge.attach(getContext());
                        r.saveSelectedDevice(address, name, input.getText().toString().trim(), r.settings());
                        input.setText("");
                        r.manualReconnect(); call.resolve();
                    } catch (Exception e) { input.setText(""); call.reject("密钥格式错误或新设备缺少密钥"); }
                }).setOnCancelListener(d -> call.reject("已取消")).show();
        });
    }
}
