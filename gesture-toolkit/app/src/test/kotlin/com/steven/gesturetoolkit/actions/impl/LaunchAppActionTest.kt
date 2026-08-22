package com.steven.gesturetoolkit.actions.impl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LaunchAppActionTest {

    @Test
    fun `id is namespaced by package name to stay unique per app`() {
        val chrome = LaunchAppAction("com.android.chrome", "Chrome")
        val camera = LaunchAppAction("com.google.android.GoogleCamera", "相機")
        assertNotEquals(chrome.id, camera.id)
        assertTrue(chrome.id.contains("com.android.chrome"))
    }

    @Test
    fun `filterLaunchableApps excludes self and duplicates, sorts by label`() {
        val input = listOf(
            InstalledAppInfo("com.example.self", "Gesture Toolkit"),
            InstalledAppInfo("com.b", "Bravo"),
            InstalledAppInfo("com.a", "Alpha"),
            InstalledAppInfo("com.a", "Alpha"),
        )
        val result = filterLaunchableApps(input, excludeSelfPackage = "com.example.self")
        assertEquals(listOf("com.a", "com.b"), result.map { it.packageName })
    }
}
