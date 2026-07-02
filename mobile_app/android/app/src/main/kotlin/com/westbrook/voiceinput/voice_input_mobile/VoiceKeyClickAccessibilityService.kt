package com.westbrook.voiceinput.voice_input_mobile

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.graphics.Path
import android.provider.Settings
import android.text.TextUtils
import android.view.accessibility.AccessibilityEvent

class VoiceKeyClickAccessibilityService : AccessibilityService() {
    override fun onServiceConnected() {
        instance = this
        super.onServiceConnected()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        if (instance === this) {
            instance = null
        }
        super.onDestroy()
    }

    companion object {
        private var instance: VoiceKeyClickAccessibilityService? = null

        fun isRunning(): Boolean = instance != null

        fun isEnabled(context: Context): Boolean {
            val expected = "${context.packageName}/${VoiceKeyClickAccessibilityService::class.java.name}"
            val enabled = Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            ) ?: return false
            val splitter = TextUtils.SimpleStringSplitter(':')
            splitter.setString(enabled)
            for (service in splitter) {
                if (service.equals(expected, ignoreCase = true)) {
                    return true
                }
            }
            return false
        }

        fun click(x: Float, y: Float, durationMs: Long): Boolean {
            val service = instance ?: return false
            val radius = CALIBRATION_DOT_SIZE_DP * service.resources.displayMetrics.density / 2f
            val nudge = radius * 0.18f
            val path = Path().apply {
                moveTo(x, y)
                lineTo(x + nudge, y + nudge)
                lineTo(x, y)
            }
            val gesture = GestureDescription.Builder()
                .addStroke(
                    GestureDescription.StrokeDescription(
                        path,
                        0,
                        durationMs.coerceIn(100L, 1000L),
                    ),
                )
                .build()
            return service.dispatchGesture(gesture, null, null)
        }

        private const val CALIBRATION_DOT_SIZE_DP = 34
    }
}
