package com.steven.gesturetoolkit.actions.impl

import android.content.Context
import android.content.Intent
import android.widget.Toast
import com.steven.gesturetoolkit.actions.Action

class LaunchAppAction(private val packageName: String, override val label: String) : Action {
    override val id = "launch_app:$packageName"
    override fun isAvailable() = true
    override fun execute(context: Context) {
        val intent = context.packageManager.getLaunchIntentForPackage(packageName)
        if (intent == null) {
            Toast.makeText(context, "找不到 App：$label", Toast.LENGTH_SHORT).show()
            return
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
    }
}

data class InstalledAppInfo(val packageName: String, val label: String)

fun filterLaunchableApps(all: List<InstalledAppInfo>, excludeSelfPackage: String): List<InstalledAppInfo> =
    all.filter { it.packageName != excludeSelfPackage }
        .distinctBy { it.packageName }
        .sortedBy { it.label }
