package com.mirrow.app.plugins;

import java.util.ArrayList;
import java.util.List;

/** One complete mode transition on the TianYan 55 protocol. */
final class TianyanCommandPlan {
    private TianyanCommandPlan() {}

    static List<byte[]> forAction(String action, int mode, int intensity,
                                  boolean heat, int vibrateIntensity) {
        List<byte[]> packets = new ArrayList<>();
        // These channels persist independently on the device. A new preset must
        // clear every old channel before enabling only the requested ones.
        packets.add(packet(0x00, 0, 0, 0, 0));
        packets.add(packet(0x03, 0, 0, 0, 0));
        packets.add(packet(0x08, 0, 0, 0, 0x0A));
        packets.add(packet(0x09, 0, 0, 0, 0));
        packets.add(packet(0x05, 0, 0, 0, 0));
        if ("stop".equals(action)) return packets;

        int m = Math.max(1, Math.min(8, mode));
        int p3 = new int[]{0, 0x40, 0xA0, 0xFF}[Math.max(0, Math.min(3, intensity))];
        int vi = Math.max(1, Math.min(10, vibrateIntensity > 0 ? vibrateIntensity : intensity));
        switch (action) {
            case "stretch":
            case "suck":
            case "combo":
            case "stretch+suck":
            case "vibrate":
            case "stretch+vibrate":
            case "stretch+suck+vibrate":
            case "heat":
                break;
            default:
                throw new IllegalArgumentException("Unknown TianYan action");
        }
        // 0x85 is the device's ACK, never a command. Heat ON is 55 05 01 37 00 00.
        if (heat || "heat".equals(action)) packets.add(packet(0x05, 1, 0x37, 0, 0));
        if ("stretch".equals(action) || "combo".equals(action)
                || "stretch+suck".equals(action) || "stretch+vibrate".equals(action)
                || "stretch+suck+vibrate".equals(action)) {
            packets.add(packet(0x08, 0, p3, m, 0x0A));
        }
        if ("suck".equals(action) || "combo".equals(action)
                || "stretch+suck".equals(action) || "stretch+suck+vibrate".equals(action)) {
            packets.add(packet(0x09, 0, p3, m, 0));
        }
        if ("vibrate".equals(action) || "stretch+vibrate".equals(action)
                || "stretch+suck+vibrate".equals(action)) {
            packets.add(packet(0x03, 0, 0, 1, vi));
        }
        return packets;
    }

    private static byte[] packet(int command, int p2, int p3, int mode, int flag) {
        return new byte[]{0x55, (byte) command, (byte) p2, (byte) p3, (byte) mode, (byte) flag};
    }
}
