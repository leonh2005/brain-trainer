package com.steven.gesturetoolkit.ui

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.steven.gesturetoolkit.actions.AppActions

@Composable
fun ActionListScreen() {
    val context = LocalContext.current
    var refreshTick by remember { mutableStateOf(0) }
    val serviceEnabled = remember(refreshTick) { isGestureToolkitAccessibilityServiceEnabled(context) }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Gesture Toolkit", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(12.dp))

        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp)) {
                Text(if (serviceEnabled) "無障礙服務：已啟用" else "無障礙服務：尚未啟用")
                Spacer(Modifier.height(8.dp))
                Row {
                    Button(onClick = {
                        context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                    }) {
                        Text("前往設定")
                    }
                    Spacer(Modifier.width(8.dp))
                    Button(onClick = { refreshTick++ }) {
                        Text("重新整理狀態")
                    }
                }
            }
        }

        Spacer(Modifier.height(16.dp))

        LazyColumn {
            items(AppActions.registry.actions) { action ->
                Row(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(action.label)
                    Button(
                        onClick = { action.execute(context) },
                        enabled = action.isAvailable(),
                    ) {
                        Text("測試")
                    }
                }
            }
        }
    }
}

private fun isGestureToolkitAccessibilityServiceEnabled(context: Context): Boolean {
    val am = context.getSystemService(Context.ACCESSIBILITY_SERVICE) as AccessibilityManager
    return am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
        .any { it.resolveInfo.serviceInfo.packageName == context.packageName }
}
