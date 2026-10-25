package com.mirrow.app.plugins;

import android.content.Context;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothProfile;
import android.util.Log;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Android BLE 玩具控制器。
 * TianYan 55 协议（iOS+buttplug 验证） + XXD 9 字节 XOR 加密。
 */
public class BLEController {
    private static final String TAG = "MIRROW_BLE";

    // ── XXD Lush12N 设备常量 ──
    private static final String XXD_MAC = "";
    private static final UUID XXD_SERVICE_UUID = UUID.fromString("00000003-0000-1000-8000-00805f9b34fb");

    // ── TianYan HA252A 设备常量 ──
    private static final String DEFAULT__tianyanMac = "";
    private static String _tianyanMac = DEFAULT__tianyanMac;
    private static final UUID TIANYAN_SERVICE_UUID = UUID.fromString("0000ffe1-0000-1000-8000-00805f9b34fb");

    /** 开源适配：从后端 relay params 下发 MAC，覆盖默认值 */
    public static void setTianyanMac(String mac) {
        if (mac != null && !mac.isEmpty()) {
            _tianyanMac = mac;
        }
    }

    // Legacy helpers remain for binary compatibility; new requests use tianyanApply.
    private static final byte[] INTENSITY_P3 = {0x00, 0x40, (byte) 0xA0, (byte) 0xFF};
    private final Context _context;
    private BluetoothAdapter _adapter;
    private BluetoothGatt _gatt;
    private CountDownLatch _connectLatch;
    private final TianyanGattSession tianyanSession;

    private static BLEController _instance;

    public static synchronized BLEController getInstance(Context context) {
        if (_instance == null) {
            _instance = new BLEController(context.getApplicationContext());
        }
        return _instance;
    }

    private BLEController(Context context) {
        _context = context;
        _adapter = BluetoothAdapter.getDefaultAdapter();
        tianyanSession = new TianyanGattSession(context, _adapter);
    }

    /** Service teardown only; backgrounding the Activity does not end the session. */
    public static synchronized void disconnectIfInitialized() {
        if (_instance != null) _instance.disconnect();
    }

    public void disconnect() {
        tianyanSession.disconnect();
        disconnectLegacy();
    }

    private synchronized void disconnectLegacy() {
        if (_gatt != null) {
            try { _gatt.disconnect(); _gatt.close(); }
            catch (Exception e) { Log.w(TAG, "disconnect error: " + e.getMessage()); }
            _gatt = null;
        }
    }

    // ═══════════════════════════════════════════════
    // XXD 9字节 XOR（不变）
    // ═══════════════════════════════════════════════

    private byte[] deriveKey(String mac) {
        String[] parts = mac.replace("-", ":").split(":");
        byte[] m = new byte[6];
        for (int i = 0; i < 6; i++) m[i] = (byte) Integer.parseInt(parts[i], 16);
        int n = 0; for (byte b : m) n ^= (b & 0xFF);
        int i = 4 ^ n;
        byte z1 = (byte)(0x80 | (i >> 4)), z2 = (byte)(0x80 | (i & 0x0F)), z3 = (byte)i;
        return new byte[]{z1, z2, z3, (byte)(z1 ^ z2 ^ z3)};
    }

    private byte[] xxdPacket(byte[] key, int logo, int cat1, int cat2, int gear) {
        byte[] d = new byte[9];
        d[0]=key[0]; d[1]=key[1]; d[2]=key[2]; d[3]=key[3];
        d[4]=(byte)logo; d[5]=(byte)cat1; d[6]=(byte)cat2; d[7]=(byte)gear;
        byte cs=0; for (int j=0;j<8;j++) cs^=d[j]; d[8]=cs;
        return d;
    }

    public String xxdVibrate(int i) { return xxdSend(101,58,48,Math.min(19,Math.max(1,i))); }
    public String xxdStop() { return xxdSend(101,58,48,0); }
    public String xxdPulse() { return xxdSend(101,61,51,5); }
    public String xxdBattery() { return xxdSend(101,63,53,0); }

    private String xxdSend(int a,int b,int c,int d){
        if (!BluetoothAdapter.checkBluetoothAddress(XXD_MAC)) return "{\"success\":false,\"error\":\"XXD device address is not configured\"}";
        try{
            byte[] k=deriveKey(XXD_MAC); byte[] p=xxdPacket(k,a,b,c,d);
            boolean ok=connectAndWrite(XXD_MAC,XXD_SERVICE_UUID,p);
            return ok?"{\"success\":true,\"content\":\"XXD gear="+d+"\"}":"{\"success\":false,\"error\":\"XXD BLE fail\"}";
        }catch(Exception e){return "{\"success\":false,\"error\":\"XXD: "+e.getMessage()+"\"}";}
    }

    // ═══════════════════════════════════════════════
    // TianYan 55 协议（iOS+buttplug 验证）
    // ═══════════════════════════════════════════════

    /** One stop-then-preset transaction; no intermediate write may fail silently. */
    public String tianyanApply(String action, int mode, int intensity,
                               boolean heat, int vibrateIntensity) {
        if (!BluetoothAdapter.checkBluetoothAddress(_tianyanMac)) return "{\"success\":false,\"error\":\"TianYan device address is not configured\"}";
        try {
            List<byte[]> packets = TianyanCommandPlan.forAction(
                    action, mode, intensity, heat, vibrateIntensity);
            List<Integer> delays = new ArrayList<>();
            for (int i = 0; i < packets.size(); i++) delays.add(100);
            boolean ok = connectAndWriteMulti(_tianyanMac, TIANYAN_SERVICE_UUID, packets, delays,
                    "stop".equals(action));
            return ok ? "{\"success\":true,\"content\":\"TianYan commands sent; device state unverified\"}"
                      : "{\"success\":false,\"error\":\"TianYan BLE write failed\"}";
        } catch (IllegalArgumentException e) {
            return "{\"success\":false,\"error\":\"Unknown TianYan action\"}";
        }
    }

    public String tianyanVibrate(int intensity) {
        int vi = Math.max(1, Math.min(10, intensity));
        byte[] c = new byte[]{0x55, 0x03, 0x00, 0x00, 0x01, (byte)vi};
        return tianyanSendSingle(c, "vibrate i="+vi);
    }

    public String tianyanVibrateStop() {
        return tianyanSendSingle(new byte[]{0x55, 0x03, 0x00, 0x00, 0x00, 0x00}, "vibrate off");
    }

    public String tianyanStretch(int mode, int intensity) {
        byte p3 = INTENSITY_P3[Math.min(3, Math.max(0, intensity))];
        byte[] c = new byte[]{0x55, 0x08, 0x00, p3, (byte)mode, 0x0A};
        return tianyanSendSingle(c, "stretch m="+mode+" i="+intensity);
    }

    public String tianyanSuck(int mode, int intensity) {
        byte p3 = INTENSITY_P3[Math.min(3, Math.max(0, intensity))];
        byte[] c = new byte[]{0x55, 0x09, 0x00, p3, (byte)mode, 0x00};
        return tianyanSendSingle(c, "suck m="+mode+" i="+intensity);
    }

    public String tianyanHeat() {
        // 0x85 是设备对 0x05 的 ACK；把 ACK 当命令下发只会“写成功”，不会加热。
        return tianyanSendSingle(new byte[]{0x55, 0x05, 0x01, 0x37, 0x00, 0x00}, "heat command sent");
    }

    /** 全通道清零: vibrate/stretch/suck/heat */
    public String tianyanStop() {
        List<byte[]> pkts = new ArrayList<>();
        List<Integer> delays = new ArrayList<>();
        pkts.add(new byte[]{0x55, 0x00, 0x00, 0x00, 0x00, 0x00}); delays.add(50);
        pkts.add(new byte[]{0x55, 0x03, 0x00, 0x00, 0x00, 0x00}); delays.add(50);
        pkts.add(new byte[]{0x55, 0x08, 0x00, 0x00, 0x00, 0x0A}); delays.add(50);
        pkts.add(new byte[]{0x55, 0x09, 0x00, 0x00, 0x00, 0x00}); delays.add(50);
        pkts.add(new byte[]{0x55, 0x05, 0x00, 0x00, 0x00, 0x00}); delays.add(0);
        boolean ok = connectAndWriteMulti(_tianyanMac, TIANYAN_SERVICE_UUID, pkts, delays, true);
        return ok ? "{\"success\":true,\"content\":\"all stop\"}"
                  : "{\"success\":false,\"error\":\"TianYan stop fail\"}";
    }

    /** heat + combo */
    public String tianyanComboWithHeat(int mode, int intensity) {
        byte p3 = INTENSITY_P3[Math.min(3, Math.max(0, intensity))];
        if (p3 == 0x00) p3 = (byte)0xFF;
        List<byte[]> pkts = new ArrayList<>();
        List<Integer> delays = new ArrayList<>();
        pkts.add(new byte[]{0x55, 0x05, 0x01, 0x37, 0x00, 0x00}); delays.add(100);
        pkts.add(new byte[]{0x55, 0x08, 0x00, p3, (byte)mode, 0x0A}); delays.add(100);
        pkts.add(new byte[]{0x55, 0x09, 0x00, p3, (byte)mode, 0x00}); delays.add(0);
        boolean ok = connectAndWriteMulti(_tianyanMac, TIANYAN_SERVICE_UUID, pkts, delays);
        return ok ? "{\"success\":true,\"content\":\"combo+heat m="+mode+" i="+intensity+"\"}"
                  : "{\"success\":false,\"error\":\"TianYan combo+heat fail\"}";
    }

    /** heat + stretch */
    public String tianyanStretchWithHeat(int mode, int intensity) {
        byte p3 = INTENSITY_P3[Math.min(3, Math.max(0, intensity))];
        List<byte[]> pkts = new ArrayList<>();
        List<Integer> delays = new ArrayList<>();
        pkts.add(new byte[]{0x55, 0x05, 0x01, 0x37, 0x00, 0x00}); delays.add(100);
        pkts.add(new byte[]{0x55, 0x08, 0x00, p3, (byte)mode, 0x0A}); delays.add(0);
        boolean ok = connectAndWriteMulti(_tianyanMac, TIANYAN_SERVICE_UUID, pkts, delays);
        return ok ? "{\"success\":true,\"content\":\"stretch+heat m="+mode+" i="+intensity+"\"}"
                  : "{\"success\":false,\"error\":\"TianYan stretch+heat fail\"}";
    }

    /** heat + stretch + suck + vibrate；整套命令在同一 GATT 会话内发送。 */
    public String tianyanFullWithHeat(int mode, int intensity, int vibrateIntensity) {
        byte p3 = INTENSITY_P3[Math.min(3, Math.max(0, intensity))];
        if (p3 == 0x00) p3 = (byte)0xFF;
        int vi = Math.max(1, Math.min(10, vibrateIntensity));
        List<byte[]> pkts = new ArrayList<>();
        List<Integer> delays = new ArrayList<>();
        pkts.add(new byte[]{0x55, 0x05, 0x01, 0x37, 0x00, 0x00}); delays.add(100);
        pkts.add(new byte[]{0x55, 0x08, 0x00, p3, (byte)mode, 0x0A}); delays.add(100);
        pkts.add(new byte[]{0x55, 0x09, 0x00, p3, (byte)mode, 0x00}); delays.add(100);
        pkts.add(new byte[]{0x55, 0x03, 0x00, 0x00, 0x01, (byte)vi}); delays.add(0);
        boolean ok = connectAndWriteMulti(_tianyanMac, TIANYAN_SERVICE_UUID, pkts, delays);
        return ok ? "{\"success\":true,\"content\":\"full+heat m="+mode+" i="+intensity+" v="+vi+"\"}"
                  : "{\"success\":false,\"error\":\"TianYan full+heat fail\"}";
    }

    // ═══════════════════════════════════════════════
    // BLE 连接 + 写入
    // ═══════════════════════════════════════════════

    private String tianyanSendSingle(byte[] cmd, String desc) {
        try {
            boolean ok = connectAndWrite(_tianyanMac, TIANYAN_SERVICE_UUID, cmd);
            return ok ? "{\"success\":true,\"content\":\""+desc+"\"}"
                      : "{\"success\":false,\"error\":\"TianYan BLE fail\"}";
        } catch (Exception e) {
            return "{\"success\":false,\"error\":\"TianYan: "+e.getMessage()+"\"}";
        }
    }

    private synchronized boolean connectAndWrite(String mac, UUID svcUuid, byte[] data) {
        if (TIANYAN_SERVICE_UUID.equals(svcUuid)) {
            List<byte[]> packets = new ArrayList<>();
            packets.add(data);
            List<Integer> delays = new ArrayList<>();
            delays.add(0);
            return connectAndWriteMulti(mac, svcUuid, packets, delays);
        }
        if (_adapter == null || !_adapter.isEnabled()) { Log.w(TAG, "BT off"); return false; }
        disconnectLegacy();
        BluetoothDevice dev = _adapter.getRemoteDevice(mac);
        if (dev == null) return false;
        _connectLatch = new CountDownLatch(1);
        final boolean[] ok = {false};
        _gatt = dev.connectGatt(_context, false, new BluetoothGattCallback() {
            @Override public void onConnectionStateChange(BluetoothGatt g, int s, int ns) {
                if (ns == BluetoothProfile.STATE_CONNECTED) g.discoverServices();
                else if (ns == BluetoothProfile.STATE_DISCONNECTED) _connectLatch.countDown();
            }
            @Override public void onServicesDiscovered(BluetoothGatt g, int s) {
                if (s != BluetoothGatt.GATT_SUCCESS) { _connectLatch.countDown(); return; }
                BluetoothGattCharacteristic ch = null;
                for (BluetoothGattService svc : g.getServices()) {
                    ch = svc.getCharacteristic(svcUuid);
                    if (ch != null) break;
                }
                if (ch == null) { _connectLatch.countDown(); return; }
                ch.setValue(data);
                ch.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE);
                ok[0] = g.writeCharacteristic(ch);
                if (ok[0]) { try { Thread.sleep(200); } catch (Exception ignored) {} }
                _connectLatch.countDown();
            }
        });
        try { _connectLatch.await(10, TimeUnit.SECONDS); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        boolean sent = ok[0];
        disconnectLegacy();
        return sent;
    }

    private boolean connectAndWriteMulti(String mac, UUID svcUuid,
                                                       List<byte[]> packets, List<Integer> delaysMs) {
        return connectAndWriteMulti(mac, svcUuid, packets, delaysMs, false);
    }

    private boolean connectAndWriteMulti(String mac, UUID svcUuid,
                                        List<byte[]> packets, List<Integer> delaysMs, boolean closeAfter) {
        if (_adapter == null || !_adapter.isEnabled()) {
            Log.w(TAG, "BT off");
            tianyanSession.disconnect();
            return false;
        }
        return tianyanSession.execute(mac, svcUuid, packets, delaysMs, closeAfter);
    }
}
