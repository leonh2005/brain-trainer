package com.steven.gesturetoolkit.actions.impl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VolumeActionsTest {

    @Test
    fun `volume actions are always available with valid metadata`() {
        val actions = listOf(VolumeUpAction(), VolumeDownAction())
        actions.forEach {
            assertTrue(it.isAvailable())
            assertFalse(it.id.isBlank())
            assertFalse(it.label.isBlank())
        }
        assertEquals(2, actions.map { it.id }.toSet().size)
    }
}
