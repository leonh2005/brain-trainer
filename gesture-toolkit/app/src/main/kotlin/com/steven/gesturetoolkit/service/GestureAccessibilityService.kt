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
        private const val TAG = "GestureA11yService"

        var instance: GestureAccessibilityService? = null
            private set

        // 加速度計取樣延遲。SENSOR_DELAY_GAME(~50Hz) 若實機測試發現敲擊常常沒反應，
        // 優先試 SENSOR_DELAY_FASTEST（API 31+ 免額外權限最高約 200Hz），
        // 不要先調 IMPACT_THRESHOLD——取樣太慢跟閾值不對從外部看起來一樣，但成因不同。
        private const val ACCELEROMETER_SAMPLING_DELAY = SensorManager.SENSOR_DELAY_FASTEST
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
            sm.registerListener(this, accelerometer, ACCELEROMETER_SAMPLING_DELAY)
        } else {
            android.util.Log.w(TAG, "裝置沒有加速度計，敲背手勢功能停用")
        }
    }

    override fun onSensorChanged(event: SensorEvent) {
        val timestampMs = event.timestamp / 1_000_000L
        val triggered = backTapDetector.onSensorData(event.values[0], event.values[1], event.values[2], timestampMs)
        if (triggered) {
            android.util.Log.d(TAG, "Back Tap 觸發，timestampMs=$timestampMs")
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
