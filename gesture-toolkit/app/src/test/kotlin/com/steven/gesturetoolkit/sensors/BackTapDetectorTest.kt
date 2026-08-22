package com.steven.gesturetoolkit.sensors

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackTapDetectorTest {

    /** 模擬一次「衝擊」：在 startMs 時淨加速度衝到 peakNetAccel，經過 durationMs 後降回重力基準。
     * 回傳最後一筆（降回基準那筆）的 onSensorData 結果。 */
    private fun feedImpact(detector: BackTapDetector, startMs: Long, durationMs: Long, peakNetAccel: Float): Boolean {
        detector.onSensorData(0f, 0f, 9.81f + peakNetAccel, startMs)
        return detector.onSensorData(0f, 0f, 9.81f, startMs + durationMs)
    }

    @Test
    fun `two narrow impacts 300ms apart trigger back tap`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
        assertTrue(feedImpact(detector, startMs = 300, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `single impact does not trigger`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `sustained shake longer than max impact duration is not counted as a tap`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 300, peakNetAccel = 30f))
        assertFalse(feedImpact(detector, startMs = 350, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `two impacts 900ms apart do not trigger (gap too large)`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
        assertFalse(feedImpact(detector, startMs = 900, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `no repeated trigger within cooldown window`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
        assertTrue(feedImpact(detector, startMs = 300, durationMs = 30, peakNetAccel = 30f))
        assertFalse(feedImpact(detector, startMs = 400, durationMs = 30, peakNetAccel = 30f))
        assertFalse(feedImpact(detector, startMs = 700, durationMs = 30, peakNetAccel = 30f))
    }
}
