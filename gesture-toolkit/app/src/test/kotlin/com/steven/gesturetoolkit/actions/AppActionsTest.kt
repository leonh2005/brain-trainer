package com.steven.gesturetoolkit.actions

import org.junit.Assert.assertEquals
import org.junit.Test

class AppActionsTest {
    @Test
    fun `app registry has 8 actions with unique ids`() {
        val ids = AppActions.registry.actions.map { it.id }
        assertEquals(8, ids.size)
        assertEquals(ids.size, ids.toSet().size)
    }
}
