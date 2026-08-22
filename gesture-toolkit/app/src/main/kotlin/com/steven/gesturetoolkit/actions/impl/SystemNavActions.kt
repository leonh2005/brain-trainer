package com.steven.gesturetoolkit.actions.impl

import android.accessibilityservice.AccessibilityService
import android.content.Context
import com.steven.gesturetoolkit.actions.Action
import com.steven.gesturetoolkit.service.GestureAccessibilityService

private fun performGlobalActionOrNoop(globalAction: Int) {
    GestureAccessibilityService.instance?.performGlobalAction(globalAction)
}

class ScreenshotAction : Action {
    override val id = "screenshot"
    override val label = "螢幕截圖"
    override fun isAvailable() = GestureAccessibilityService.instance != null
    override fun execute(context: Context) {
        performGlobalActionOrNoop(AccessibilityService.GLOBAL_ACTION_TAKE_SCREENSHOT)
    }
}

class LockScreenAction : Action {
    override val id = "lock_screen"
    override val label = "鎖定螢幕"
    override fun isAvailable() = GestureAccessibilityService.instance != null
    override fun execute(context: Context) {
        performGlobalActionOrNoop(AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN)
    }
}

class HomeAction : Action {
    override val id = "home"
    override val label = "回到主畫面"
    override fun isAvailable() = GestureAccessibilityService.instance != null
    override fun execute(context: Context) {
        performGlobalActionOrNoop(AccessibilityService.GLOBAL_ACTION_HOME)
    }
}

class BackAction : Action {
    override val id = "back"
    override val label = "返回"
    override fun isAvailable() = GestureAccessibilityService.instance != null
    override fun execute(context: Context) {
        performGlobalActionOrNoop(AccessibilityService.GLOBAL_ACTION_BACK)
    }
}

class RecentsAction : Action {
    override val id = "recents"
    override val label = "最近使用App"
    override fun isAvailable() = GestureAccessibilityService.instance != null
    override fun execute(context: Context) {
        performGlobalActionOrNoop(AccessibilityService.GLOBAL_ACTION_RECENTS)
    }
}
