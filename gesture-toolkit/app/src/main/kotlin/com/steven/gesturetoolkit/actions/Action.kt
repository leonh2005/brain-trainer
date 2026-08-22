package com.steven.gesturetoolkit.actions

import android.content.Context

interface Action {
    val id: String
    val label: String
    fun isAvailable(): Boolean
    fun execute(context: Context)
}
