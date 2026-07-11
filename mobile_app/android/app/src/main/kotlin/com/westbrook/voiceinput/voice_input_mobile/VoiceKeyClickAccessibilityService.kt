package com.westbrook.voiceinput.voice_input_mobile

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityService.GestureResultCallback
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.graphics.Path
import android.os.Handler
import android.os.Build
import android.os.Looper
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
        stopHold()
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

        fun startHold(context: Context): String {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
                return "voice_hold_requires_android_8"
            }
            val service = instance ?: return "accessibility_not_running"
            val prefs = context.getSharedPreferences(
                VoiceClickCalibrationService.PREFS_NAME,
                Context.MODE_PRIVATE,
            )
            if (!prefs.contains(VoiceClickCalibrationService.KEY_X) ||
                !prefs.contains(VoiceClickCalibrationService.KEY_Y)
            ) {
                return "voice_click_point_missing"
            }
            val x = prefs.getFloat(VoiceClickCalibrationService.KEY_X, -1f)
            val y = prefs.getFloat(VoiceClickCalibrationService.KEY_Y, -1f)
            if (x < 0f || y < 0f) {
                return "voice_click_point_invalid"
            }
            if (holdActive && !holdReleaseRequested) {
                return "ok"
            }
            val generation = ++holdGeneration
            holdActive = true
            holdReleaseRequested = false
            val offset = HOLD_OFFSET_DP * service.resources.displayMetrics.density
            val firstEndX = x + offset
            val firstPath = Path().apply {
                moveTo(x, y)
                lineTo(firstEndX, y)
            }
            val firstStroke = GestureDescription.StrokeDescription(
                firstPath,
                0,
                HOLD_SEGMENT_MS,
                true,
            )
            dispatchHoldSegment(
                service = service,
                stroke = firstStroke,
                endX = firstEndX,
                y = y,
                originalX = x,
                offsetX = firstEndX,
                generation = generation,
            )
            return "ok"
        }

        fun stopHold() {
            if (holdActive) {
                holdReleaseRequested = true
            }
        }

        private fun dispatchHoldSegment(
            service: VoiceKeyClickAccessibilityService,
            stroke: GestureDescription.StrokeDescription,
            endX: Float,
            y: Float,
            originalX: Float,
            offsetX: Float,
            generation: Int,
        ) {
            if (!holdActive || generation != holdGeneration || instance !== service) {
                return
            }
            val gesture = GestureDescription.Builder()
                .addStroke(stroke)
                .build()
            val dispatched = service.dispatchGesture(
                gesture,
                object : GestureResultCallback() {
                    override fun onCompleted(gestureDescription: GestureDescription?) {
                        if (generation != holdGeneration || instance !== service) {
                            return
                        }
                        if (holdReleaseRequested) {
                            dispatchHoldRelease(service, stroke, endX, y, generation)
                            return
                        }
                        val nextEndX = if (endX == originalX) offsetX else originalX
                        val nextPath = Path().apply {
                            moveTo(endX, y)
                            lineTo(nextEndX, y)
                        }
                        val nextStroke = stroke.continueStroke(
                            nextPath,
                            0,
                            HOLD_SEGMENT_MS,
                            true,
                        )
                        dispatchHoldSegment(
                            service,
                            nextStroke,
                            nextEndX,
                            y,
                            originalX,
                            offsetX,
                            generation,
                        )
                    }

                    override fun onCancelled(gestureDescription: GestureDescription?) {
                        if (generation == holdGeneration) {
                            holdActive = false
                            holdReleaseRequested = false
                            if (instance === service) {
                                MainActivity.sendOverlayDiagnostic("voice_click_dispatch_failed")
                            }
                        }
                    }
                },
                mainHandler,
            )
            if (!dispatched) {
                holdActive = false
                holdReleaseRequested = false
                MainActivity.sendOverlayDiagnostic("voice_click_dispatch_failed")
            }
        }

        private fun dispatchHoldRelease(
            service: VoiceKeyClickAccessibilityService,
            stroke: GestureDescription.StrokeDescription,
            x: Float,
            y: Float,
            generation: Int,
        ) {
            val releasePath = Path().apply {
                moveTo(x, y)
                lineTo(x + HOLD_RELEASE_NUDGE_PX, y)
            }
            val releaseStroke = stroke.continueStroke(
                releasePath,
                0,
                HOLD_RELEASE_MS,
                false,
            )
            val releaseGesture = GestureDescription.Builder()
                .addStroke(releaseStroke)
                .build()
            val dispatched = service.dispatchGesture(
                releaseGesture,
                object : GestureResultCallback() {
                    override fun onCompleted(gestureDescription: GestureDescription?) {
                        finishHold(generation)
                    }

                    override fun onCancelled(gestureDescription: GestureDescription?) {
                        finishHold(generation)
                    }
                },
                mainHandler,
            )
            if (!dispatched) {
                finishHold(generation)
            }
        }

        private fun finishHold(generation: Int) {
            if (generation == holdGeneration) {
                holdActive = false
                holdReleaseRequested = false
            }
        }

        private const val CALIBRATION_DOT_SIZE_DP = 34
        private const val HOLD_SEGMENT_MS = 500L
        private const val HOLD_RELEASE_MS = 40L
        private const val HOLD_OFFSET_DP = 2f
        private const val HOLD_RELEASE_NUDGE_PX = 0.5f
        private val mainHandler = Handler(Looper.getMainLooper())
        private var holdActive = false
        private var holdReleaseRequested = false
        private var holdGeneration = 0
    }
}
