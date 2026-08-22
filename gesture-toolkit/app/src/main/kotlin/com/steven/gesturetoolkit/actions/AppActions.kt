package com.steven.gesturetoolkit.actions

import com.steven.gesturetoolkit.actions.impl.BackAction
import com.steven.gesturetoolkit.actions.impl.HomeAction
import com.steven.gesturetoolkit.actions.impl.LockScreenAction
import com.steven.gesturetoolkit.actions.impl.RecentsAction
import com.steven.gesturetoolkit.actions.impl.ScreenshotAction
import com.steven.gesturetoolkit.actions.impl.VolumeDownAction
import com.steven.gesturetoolkit.actions.impl.VolumeUpAction

object AppActions {
    val registry = ActionRegistry(
        listOf(
            ScreenshotAction(),
            LockScreenAction(),
            HomeAction(),
            BackAction(),
            RecentsAction(),
            VolumeUpAction(),
            VolumeDownAction(),
        )
    )
}
