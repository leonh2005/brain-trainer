package com.steven.gesturetoolkit.sensors

import kotlin.math.abs
import kotlin.math.sqrt

class BackTapDetector(
    private val impactThreshold: Float = IMPACT_THRESHOLD,
    private val maxImpactDurationMs: Long = MAX_IMPACT_DURATION_MS,
    private val minGapMs: Long = MIN_GAP_MS,
    private val maxGapMs: Long = MAX_GAP_MS,
    private val cooldownMs: Long = COOLDOWN_MS,
) {
    companion object {
        const val IMPACT_THRESHOLD = 25f
        const val MAX_IMPACT_DURATION_MS = 100L
        const val MIN_GAP_MS = 100L
        const val MAX_GAP_MS = 600L
        const val COOLDOWN_MS = 1000L
        private const val GRAVITY = 9.81f
    }

    private var impactStartMs: Long? = null
    private var impactPeak: Float = 0f
    private var pendingTap: TapEvent? = null
    private var lastTriggerMs: Long? = null

    /** 餵一筆加速度計原始數值，回傳這筆資料是否讓「敲背兩下」的條件成立。 */
    fun onSensorData(x: Float, y: Float, z: Float, timestampMs: Long): Boolean {
        val magnitude = sqrt(x * x + y * y + z * z)
        val netAccel = abs(magnitude - GRAVITY)

        if (netAccel >= impactThreshold) {
            if (impactStartMs == null) {
                impactStartMs = timestampMs
                impactPeak = netAccel
            } else if (netAccel > impactPeak) {
                impactPeak = netAccel
            }
            return false
        }

        val start = impactStartMs ?: return false
        impactStartMs = null
        val duration = timestampMs - start
        if (duration > maxImpactDurationMs) return false

        return onTapEvent(TapEvent(timestampMs = start, peakMagnitude = impactPeak))
    }

    private fun onTapEvent(tap: TapEvent): Boolean {
        lastTriggerMs?.let { if (tap.timestampMs - it < cooldownMs) return false }

        val previous = pendingTap
        if (previous != null) {
            val gap = tap.timestampMs - previous.timestampMs
            if (gap in minGapMs..maxGapMs) {
                pendingTap = null
                lastTriggerMs = tap.timestampMs
                return true
            }
        }
        pendingTap = tap
        return false
    }
}
