package com.steven.gesturetoolkit.actions

import android.content.Context
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ActionRegistryTest {

    private fun fakeAction(actionId: String, actionLabel: String = "Test") = object : Action {
        override val id = actionId
        override val label = actionLabel
        override fun isAvailable() = true
        override fun execute(context: Context) {}
    }

    @Test
    fun `get returns action by id`() {
        val registry = ActionRegistry(listOf(fakeAction("a"), fakeAction("b")))
        assertEquals("a", registry.get("a")?.id)
    }

    @Test
    fun `get returns null for unknown id`() {
        val registry = ActionRegistry(listOf(fakeAction("a")))
        assertNull(registry.get("z"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun `duplicate ids throw on construction`() {
        ActionRegistry(listOf(fakeAction("a"), fakeAction("a")))
    }
}
