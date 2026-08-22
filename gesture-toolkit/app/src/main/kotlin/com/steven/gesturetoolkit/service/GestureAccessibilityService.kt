package com.steven.gesturetoolkit.service

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.view.accessibility.AccessibilityEvent
import com.steven.gesturetoolkit.actions.AppActions
import com.steven.gesturetoolkit.sensors.BackTapDetector

class GestureAccessibilityService : AccessibilityService(), SensorEventListener {

    companion object {
        var instance: GestureAccessibilityService? = null
            private set
    }

    private val backTapDetector = BackTapDetector()
    private var sensorManager: SensorManager? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        val sm = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        sensorManager = sm
        val accelerometer = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        if (accelerometer != null) {
            sm.registerListener(this, accelerometer, SensorManager.SENSOR_DELAY_GAME)
        }
    }

    override fun onSensorChanged(event: SensorEvent) {
        val timestampMs = event.timestamp / 1_000_000L
        val triggered = backTapDetector.onSensorData(event.values[0], event.values[1], event.values[2], timestampMs)
        if (triggered) {
            val action = AppActions.registry.get("screenshot")
            if (action != null && action.isAvailable()) {
                action.execute(this)
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {
        // 不需要處理精度變化
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // 本 app 只用這個服務執行 performGlobalAction，不需要監聽任何無障礙事件
    }

    override fun onInterrupt() {
        // 系統中斷服務時呼叫，本 app 無需特別處理
    }

    override fun onDestroy() {
        super.onDestroy()
        sensorManager?.unregisterListener(this)
        if (instance === this) {
            instance = null
        }
    }
}
