package com.mirrow.step

import android.util.Log
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.ServerSocket
import java.net.Socket

class StepHttpServer(private val port: Int) {
    private var serverSocket: ServerSocket? = null
    private var running = false
    var stepsProvider: (() -> Long)? = null

    fun start() {
        running = true
        Thread {
            try {
                serverSocket = ServerSocket(port)
                Log.i(TAG, "Step server started on port $port")
                while (running) {
                    try {
                        val client = serverSocket?.accept() ?: break
                        handleClient(client)
                    } catch (e: Exception) {
                        if (running) Log.e(TAG, "Accept: ${e.message}")
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Server: ${e.message}")
            }
        }.start()
    }

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
    }

    private fun handleClient(socket: Socket) {
        try {
            val reader = BufferedReader(InputStreamReader(socket.getInputStream()))
            val writer = OutputStreamWriter(socket.outputStream)
            val requestLine = reader.readLine() ?: return
            val parts = requestLine.split(" ")
            if (parts.size < 2) return
            val path = parts[1]

            // Read headers (ignore body)
            var line = reader.readLine()
            while (!line.isNullOrEmpty()) line = reader.readLine()

            val body = when (path) {
                "/steps" -> {
                    val total = stepsProvider?.invoke() ?: -1L
                    """{"steps":$total,"date":"${java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.getDefault()).format(java.util.Date())}"}"""
                }
                "/health", "/" -> """{"status":"ok","service":"MIRROW Step Bridge"}"""
                else -> """{"error":"not found"}"""
            }

            val bodyBytes = body.toByteArray(Charsets.UTF_8)
            writer.write("HTTP/1.1 200 OK\r\n")
            writer.write("Content-Type: application/json; charset=utf-8\r\n")
            writer.write("Access-Control-Allow-Origin: *\r\n")
            writer.write("Content-Length: ${bodyBytes.size}\r\n")
            writer.write("\r\n")
            writer.write(body)
            writer.flush()
        } catch (e: Exception) {
            Log.e(TAG, "Handle: ${e.message}")
        } finally {
            try { socket.close() } catch (_: Exception) {}
        }
    }

    companion object {
        private const val TAG = "MIRROW_STEP_HTTP"
    }
}
