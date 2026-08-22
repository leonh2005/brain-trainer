package com.steven.gesturetoolkit.actions.impl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class SystemNavActionsTest {

    private fun allActions() = listOf(
        ScreenshotAction(), LockScreenAction(), HomeAction(), BackAction(), RecentsAction()
    )

    @Test
    fun `actions have unique non-blank ids and labels`() {
        val actions = allActions()
        val ids = actions.map { it.id }
        assertEquals(ids.size, ids.toSet().size)
        actions.forEach {
            assertFalse(it.id.isBlank())
            assertFalse(it.label.isBlank())
        }
    }

    @Test
    fun `isAvailable is false when accessibility service not connected`() {
        // 單元測試環境沒有真正連線的 GestureAccessibilityService，instance 必為 null
        allActions().forEach { assertFalse(it.isAvailable()) }
    }
}
