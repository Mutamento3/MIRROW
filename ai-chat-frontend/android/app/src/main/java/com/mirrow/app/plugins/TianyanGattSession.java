package com.mirrow.app.plugins;

import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothProfile;
import android.content.Context;
import android.util.Log;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;

/** Native, application-context BLE owner; Activity/WS background transitions do not close it. */
final class TianyanGattSession {
    private static final String TAG = "MIRROW_BLE";
    private final ToyGattSession session;

    TianyanGattSession(Context context, BluetoothAdapter adapter) {
        session = new ToyGattSession((address, uuid, events) -> {
            BluetoothDevice device = adapter.getRemoteDevice(address);
            AndroidTransport transport = new AndroidTransport(uuid, events);
            BluetoothGatt gatt = device.connectGatt(context, false, transport,
                    BluetoothDevice.TRANSPORT_LE);
            transport.attach(gatt);
            return gatt == null ? null : transport;
        }, 25000, 200);
    }

    boolean execute(String address, UUID characteristic, List<byte[]> packets,
                    List<Integer> delays, boolean closeAfter) {
        boolean ok = session.execute(address, characteristic, packets, delays, closeAfter);
        Log.i(TAG, ok ? (closeAfter ? "TianYan stop batch sent; session closed"
                : "TianYan batch sent; session retained") : "TianYan batch failed; no write replay");
        return ok;
    }

    void disconnect() { session.disconnect(); }

    private static final class AndroidTransport extends BluetoothGattCallback
            implements ToyGattSession.Transport {
        private final UUID uuid;
        private final ToyGattSession.Events events;
        private final AtomicBoolean closed = new AtomicBoolean();
        private volatile BluetoothGatt gatt;
        private volatile BluetoothGattCharacteristic characteristic;

        AndroidTransport(UUID uuid, ToyGattSession.Events events) {
            this.uuid = uuid;
            this.events = events;
        }

        void attach(BluetoothGatt value) {
            gatt = value;
            if (closed.get() && value != null) closeGatt(value);
        }

        @Override public void onConnectionStateChange(BluetoothGatt g, int status, int state) {
            if (closed.get()) return;
            if (status != BluetoothGatt.GATT_SUCCESS || state == BluetoothProfile.STATE_DISCONNECTED) {
                // Numeric status only: no device address or uncontrolled exception text.
                Log.w(TAG, "TianYan connection ended; status=" + status);
                close();
                events.disconnected();
            } else if (state == BluetoothProfile.STATE_CONNECTED && !g.discoverServices()) {
                close();
                events.disconnected();
            }
        }

        @Override public void onServicesDiscovered(BluetoothGatt g, int status) {
            if (closed.get()) return;
            if (status == BluetoothGatt.GATT_SUCCESS) {
                for (BluetoothGattService service : g.getServices()) {
                    characteristic = service.getCharacteristic(uuid);
                    if (characteristic != null) break;
                }
            }
            if (status != BluetoothGatt.GATT_SUCCESS || characteristic == null) {
                close();
                events.disconnected();
                return;
            }
            characteristic.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE);
            events.ready();
        }

        @Override public void onCharacteristicWrite(BluetoothGatt g,
                BluetoothGattCharacteristic written, int status) {
            if (!closed.get() && uuid.equals(written.getUuid())) {
                if (status != BluetoothGatt.GATT_SUCCESS) {
                    Log.w(TAG, "TianYan write failed; status=" + status);
                }
                events.writeComplete(status == BluetoothGatt.GATT_SUCCESS);
            }
        }

        @Override public boolean write(byte[] packet) {
            BluetoothGatt active = gatt;
            BluetoothGattCharacteristic target = characteristic;
            if (closed.get() || active == null || target == null) return false;
            target.setValue(packet);
            return active.writeCharacteristic(target);
        }

        @Override public void close() {
            if (closed.compareAndSet(false, true) && gatt != null) closeGatt(gatt);
        }

        private void closeGatt(BluetoothGatt value) {
            try { value.disconnect(); } catch (Exception ignored) {}
            try { value.close(); } catch (Exception ignored) {}
        }
    }
}
