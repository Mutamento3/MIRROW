package com.mirrow.app.plugins;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** App-owned command queue. Successful commands keep their connection; failures never replay writes. */
final class ToyGattSession {
    interface Transport {
        boolean write(byte[] packet);
        void close();
    }

    interface Events {
        void ready();
        void writeComplete(boolean success);
        void disconnected();
    }

    interface Connector {
        Transport connect(String address, UUID characteristic, Events events);
    }

    private final Connector connector;
    private final long timeoutMs;
    private final long drainMs;
    private final Object stateLock = new Object();
    private Connection current;
    private long cancellationEpoch;

    ToyGattSession(Connector connector, long timeoutMs, long drainMs) {
        this.connector = connector;
        this.timeoutMs = timeoutMs;
        this.drainMs = drainMs;
    }

    /** One queue for both the WebView plugin and background Relay. */
    synchronized boolean execute(String address, UUID characteristic, List<byte[]> packets,
                                 List<Integer> delays, boolean closeAfter) {
        if (packets == null || packets.isEmpty()) return false;
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
        long epoch;
        synchronized (stateLock) { epoch = cancellationEpoch; }
        Connection connection = null;
        try {
            // Only connection establishment can retry. A partly sent motor batch cannot.
            for (int attempt = 0; attempt < 2 && connection == null; attempt++) {
                connection = acquire(address, characteristic, deadline, epoch);
                synchronized (stateLock) {
                    if (epoch != cancellationEpoch) return false;
                }
            }
            if (connection == null) return false;
            for (int i = 0; i < packets.size(); i++) {
                if (System.nanoTime() >= deadline) {
                    discard(connection);
                    return false;
                }
                Write pending = new Write();
                Transport transport;
                synchronized (stateLock) {
                    if (current != connection || connection.failed) return false;
                    connection.pending = pending;
                    transport = connection.transport;
                }
                if (!transport.write(packets.get(i)) || !await(pending.done, deadline)
                        || !pending.success) {
                    discard(connection);
                    return false;
                }
                synchronized (stateLock) {
                    if (current != connection || connection.failed) return false;
                    connection.pending = null;
                }
                int delay = i < delays.size() ? delays.get(i) : 0;
                if (i + 1 < packets.size() && delay > 0) Thread.sleep(delay);
            }
            // Android's NO_RESPONSE callback confirms stack acceptance, not radio delivery.
            if (drainMs > 0) Thread.sleep(drainMs);
            synchronized (stateLock) {
                if (current != connection || connection.failed) return false;
            }
            if (closeAfter) discard(connection);
            return true;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            if (connection == null) {
                synchronized (stateLock) { connection = current; }
            }
            if (connection != null) discard(connection);
            return false;
        } catch (RuntimeException e) {
            if (connection != null) discard(connection);
            // A connector may throw before acquire returns its connection.
            synchronized (stateLock) { connection = current; }
            if (connection != null) discard(connection);
            return false;
        }
    }

    /** Cancels in-flight work, including a connection still being established. */
    void disconnect() {
        Connection connection;
        synchronized (stateLock) {
            cancellationEpoch++;
            connection = current;
        }
        if (connection != null) discard(connection);
    }

    private Connection acquire(String address, UUID characteristic, long deadline, long epoch)
            throws InterruptedException {
        Connection previous;
        synchronized (stateLock) {
            if (epoch != cancellationEpoch || System.nanoTime() >= deadline) return null;
            previous = current;
            if (previous != null && !previous.failed && previous.ready
                    && address.equals(previous.address) && characteristic.equals(previous.characteristic)) {
                return previous;
            }
        }
        if (previous != null) discard(previous);
        Connection connection = new Connection(address, characteristic);
        synchronized (stateLock) {
            if (epoch != cancellationEpoch) return null;
            current = connection;
        }
        Transport transport = connector.connect(address, characteristic, connection);
        boolean stale;
        synchronized (stateLock) {
            connection.transport = transport;
            stale = current != connection || connection.failed;
        }
        if (transport == null || stale) {
            discard(connection);
            return null;
        }
        long readyDeadline = Math.min(deadline,
                System.nanoTime() + TimeUnit.SECONDS.toNanos(10));
        if (!await(connection.readySignal, readyDeadline)) {
            discard(connection);
            return null;
        }
        synchronized (stateLock) {
            if (current == connection && connection.ready && !connection.failed) return connection;
        }
        discard(connection);
        return null;
    }

    private boolean await(CountDownLatch latch, long deadline) throws InterruptedException {
        return latch.await(Math.max(0, deadline - System.nanoTime()), TimeUnit.NANOSECONDS);
    }

    private void discard(Connection connection) {
        Transport transport;
        synchronized (stateLock) {
            if (current == connection) current = null;
            connection.failed = true;
            connection.readySignal.countDown();
            if (connection.pending != null) connection.pending.done.countDown();
            transport = connection.transport;
            connection.transport = null;
        }
        if (transport != null) transport.close();
    }

    private static final class Write {
        final CountDownLatch done = new CountDownLatch(1);
        volatile boolean success;
    }

    private final class Connection implements Events {
        final String address;
        final UUID characteristic;
        final CountDownLatch readySignal = new CountDownLatch(1);
        Transport transport;
        Write pending;
        boolean ready;
        boolean failed;

        Connection(String address, UUID characteristic) {
            this.address = address;
            this.characteristic = characteristic;
        }

        public void ready() {
            synchronized (stateLock) {
                if (current != this || failed) return;
                ready = true;
                readySignal.countDown();
            }
        }

        public void writeComplete(boolean success) {
            synchronized (stateLock) {
                if (current != this || failed || pending == null) return;
                pending.success = success;
                pending.done.countDown();
            }
        }

        public void disconnected() { discard(this); }
    }
}
