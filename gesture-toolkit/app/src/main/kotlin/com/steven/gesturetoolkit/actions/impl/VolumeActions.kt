package com.steven.gesturetoolkit.actions.impl

import android.content.Context
import android.media.AudioManager
import com.steven.gesturetoolkit.actions.Action

class VolumeUpAction : Action {
    override val id = "volume_up"
    override val label = "音量 +"
    override fun isAvailable() = true
    override fun execute(context: Context) {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI)
    }
}

class VolumeDownAction : Action {
    override val id = "volume_down"
    override val label = "音量 -"
    override fun isAvailable() = true
    override fun execute(context: Context) {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI)
    }
}
