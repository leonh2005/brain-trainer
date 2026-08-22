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

    @Test
    fun `impact exactly at max duration boundary still counts as a tap`() {
        val detector = BackTapDetector()
        // duration 剛好等於 100ms（MAX_IMPACT_DURATION_MS），邊界值本身應該「還算」敲擊事件
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 100, peakNetAccel = 30f))
        assertTrue(feedImpact(detector, startMs = 300, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `gap exactly at min gap boundary triggers`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
        // gap 剛好等於 100ms（MIN_GAP_MS），邊界值本身應該「還算」合法間隔
        assertTrue(feedImpact(detector, startMs = 100, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `gap exactly at max gap boundary triggers`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
        // gap 剛好等於 600ms（MAX_GAP_MS），邊界值本身應該「還算」合法間隔
        assertTrue(feedImpact(detector, startMs = 600, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `net acceleration exactly at threshold counts as impact`() {
        val detector = BackTapDetector()
        // peakNetAccel 剛好等於 25f（IMPACT_THRESHOLD），邊界值本身應該「還算」超過閾值
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 25f))
        assertTrue(feedImpact(detector, startMs = 300, durationMs = 30, peakNetAccel = 25f))
    }

    @Test
    fun `triple tap triggers exactly once and resets cleanly`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
        assertTrue(feedImpact(detector, startMs = 300, durationMs = 30, peakNetAccel = 30f))
        // 第三下落在冷卻期內（1000ms 內），不該再次觸發
        assertFalse(feedImpact(detector, startMs = 600, durationMs = 30, peakNetAccel = 30f))
    }
}
