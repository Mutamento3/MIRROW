package com.mirrow.step

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Bundle
import android.util.Log
import android.widget.TextView
import android.graphics.Color
import android.os.Build
import android.view.Gravity

class MainActivity : Activity(), SensorEventListener {

    private lateinit var sensorManager: SensorManager
    private var stepSensor: Sensor? = null
    private var totalSteps: Long = 0
    private var dayStartSteps: Long = 0  // steps at start of day
    private lateinit var server: StepHttpServer
    private lateinit var statusText: TextView
    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        statusText = TextView(this).apply {
            text = "MIRROW Step\nStarting..."
            textSize = 22f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setBackgroundColor(Color.parseColor("#1a1a2e"))
        }
        setContentView(statusText)

        prefs = getSharedPreferences("steps", Context.MODE_PRIVATE)

        // Request activity recognition permission (needed on API 29+)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            if (checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION)
                != PackageManager.PERMISSION_GRANTED
            ) {
                requestPermissions(
                    arrayOf(Manifest.permission.ACTIVITY_RECOGNITION),
                    100
                )
            }
        }

        initSensor()
        startServer()
    }

    private fun initSensor() {
        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        stepSensor = sensorManager.getDefaultSensor(Sensor.TYPE_STEP_COUNTER)

        if (stepSensor == null) {
            statusText.text = "MIRROW Step\n❌ No step sensor found!"
            return
        }

        // Restore day baseline
        val savedDay = prefs.getString("day", "")
        val today = java.text.SimpleDateFormat("yyyyMMdd", java.util.Locale.getDefault())
            .format(java.util.Date())
        if (savedDay == today) {
            dayStartSteps = prefs.getLong("day_start", 0)
        }
    }

    private fun startServer() {
        server = StepHttpServer(18999)
        server.stepsProvider = {
            // Return today's steps
            if (dayStartSteps > 0 && totalSteps > dayStartSteps) {
                totalSteps - dayStartSteps
            } else {
                0
            }
        }
        server.start()
        updateStatus()
    }

    private fun updateStatus() {
        val todaySteps = if (dayStartSteps > 0 && totalSteps > dayStartSteps)
            totalSteps - dayStartSteps else 0
        statusText.text = """
            MIRROW Step Bridge
            ✅ HTTP :18999

            Today: $todaySteps steps
            Sensor: ${stepSensor?.name ?: "N/A"}

            /steps - get today's count
        """.trimIndent()
        Log.i(TAG, "Status: today=$todaySteps total=$totalSteps baseline=$dayStartSteps")
    }

    override fun onSensorChanged(event: SensorEvent) {
        if (event.sensor.type == Sensor.TYPE_STEP_COUNTER) {
            totalSteps = event.values[0].toLong()

            // Check if day changed
            val today = java.text.SimpleDateFormat("yyyyMMdd", java.util.Locale.getDefault())
                .format(java.util.Date())
            val savedDay = prefs.getString("day", "")

            if (savedDay != today) {
                // New day: reset baseline
                dayStartSteps = totalSteps
                prefs.edit()
                    .putString("day", today)
                    .putLong("day_start", dayStartSteps)
                    .apply()
            } else if (dayStartSteps == 0L) {
                // First reading of the day
                dayStartSteps = prefs.getLong("day_start", totalSteps)
                if (dayStartSteps == 0L || dayStartSteps > totalSteps) {
                    dayStartSteps = totalSteps
                    prefs.edit()
                        .putString("day", today)
                        .putLong("day_start", dayStartSteps)
                        .apply()
                }
            }

            updateStatus()
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onResume() {
        super.onResume()
        stepSensor?.let {
            sensorManager.registerListener(
                this, it,
                SensorManager.SENSOR_DELAY_NORMAL,
                5_000_000  // 5s batch
            )
        }
    }

    override fun onPause() {
        super.onPause()
        sensorManager.unregisterListener(this)
    }

    override fun onDestroy() {
        server.stop()
        sensorManager.unregisterListener(this)
        super.onDestroy()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 100 && grantResults.isNotEmpty()
            && grantResults[0] == PackageManager.PERMISSION_GRANTED
        ) {
            initSensor()
            startServer()
        }
    }

    companion object {
        private const val TAG = "MIRROW_STEP"
    }
}
