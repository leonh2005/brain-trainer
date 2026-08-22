package com.steven.gesturetoolkit.service

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent

class GestureAccessibilityService : AccessibilityService() {

    companion object {
        var instance: GestureAccessibilityService? = null
            private set
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // 本 app 只用這個服務執行 performGlobalAction，不需要監聽任何無障礙事件
    }

    override fun onInterrupt() {
        // 系統中斷服務時呼叫，本 app 無需特別處理
    }

    override fun onDestroy() {
        super.onDestroy()
        if (instance === this) {
            instance = null
        }
    }
}
