package com.westbrook.voiceinput.voice_input_mobile

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityService.GestureResultCallback
import android.accessibilityservice.GestureDescription
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Path
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.TextUtils
import android.view.accessibility.AccessibilityEvent

class VoiceKeyClickAccessibilityService : AccessibilityService() {
    private val clipboardManager by lazy {
        getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    }
    private val clipboardListener = ClipboardManager.OnPrimaryClipChangedListener {
        mainHandler.postDelayed({
            if (!trySendClipboardText()) {
                reportClipboardStatus("restricted")
                sendSelectionCandidate("accessibility_selection")
            }
        }, CLIPBOARD_READ_DELAY_MS)
    }
    private var clipboardListenerRegistered = false
    private var selectionCandidate = ""
    private var selectionCandidateAt = 0L
    private var lastClipboardStatus = ""

    override fun onServiceConnected() {
        instance = this
        super.onServiceConnected()
        refreshClipboardMonitoringInternal()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null || !MobileClipboardSync.isEnabled(this)) {
            return
        }
        if (event.isPassword || event.source?.isPassword == true) {
            selectionCandidate = ""
            return
        }
        when (event.eventType) {
            AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED -> {
                captureSelectionCandidate(event)
            }
            AccessibilityEvent.TYPE_VIEW_CLICKED,
            AccessibilityEvent.TYPE_ANNOUNCEMENT,
            -> {
                if (looksLikeCopyAction(event)) {
                    mainHandler.postDelayed({
                        if (!trySendClipboardText()) {
                            reportClipboardStatus("restricted")
                            sendSelectionCandidate("accessibility_copy")
                        }
                    }, COPY_ACTION_DELAY_MS)
                }
            }
        }
    }

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        unregisterClipboardListener()
        selectionCandidate = ""
        if (holdActive) {
            MainActivity.sendVoiceHoldState(false, "service_stopped")
        }
        stopHold("service_stopped")
        if (instance === this) {
            instance = null
        }
        super.onDestroy()
    }

    private fun refreshClipboardMonitoringInternal() {
        if (!MobileClipboardSync.isEnabled(this)) {
            unregisterClipboardListener()
            selectionCandidate = ""
            reportClipboardStatus("disabled")
            return
        }
        if (!clipboardListenerRegistered) {
            clipboardManager.addPrimaryClipChangedListener(clipboardListener)
            clipboardListenerRegistered = true
        }
        reportClipboardStatus("listening")
    }

    private fun unregisterClipboardListener() {
        if (!clipboardListenerRegistered) {
            return
        }
        runCatching {
            clipboardManager.removePrimaryClipChangedListener(clipboardListener)
        }
        clipboardListenerRegistered = false
    }

    private fun captureSelectionCandidate(event: AccessibilityEvent) {
        val fullText = event.source?.text?.toString()
            ?: event.text.firstOrNull()?.toString()
            ?: return
        val from = event.fromIndex
        val to = event.toIndex
        val selected = if (from >= 0 && to > from && to <= fullText.length) {
            fullText.substring(from, to)
        } else {
            event.text.firstOrNull()?.toString().orEmpty()
        }
        if (selected.isNotEmpty() && selected.length <= MAX_SELECTION_CHARS) {
            selectionCandidate = selected
            selectionCandidateAt = System.currentTimeMillis()
        }
    }

    private fun looksLikeCopyAction(event: AccessibilityEvent): Boolean {
        val labels = buildList {
            addAll(event.text.map { it.toString() })
            event.contentDescription?.toString()?.let(::add)
            event.source?.text?.toString()?.let(::add)
            event.source?.contentDescription?.toString()?.let(::add)
        }
        return labels.any { label ->
            val normalized = label.trim().lowercase()
            normalized == "copy" ||
                normalized.contains("copy ") ||
                normalized.contains(" copied") ||
                normalized.contains("\u590d\u5236") ||
                normalized.contains("\u5df2\u590d\u5236")
        }
    }

    private fun trySendClipboardText(): Boolean {
        val description = runCatching { clipboardManager.primaryClipDescription }.getOrNull()
            ?: return false
        if (description.extras?.getBoolean(SENSITIVE_CLIP_KEY, false) == true) {
            selectionCandidate = ""
            return true
        }
        val isText = description.hasMimeType(ClipDescription.MIMETYPE_TEXT_PLAIN) ||
            description.hasMimeType(ClipDescription.MIMETYPE_TEXT_HTML)
        if (!isText) {
            return true
        }
        val clip = runCatching { clipboardManager.primaryClip }.getOrNull() ?: return false
        if (clip.itemCount == 0) {
            return false
        }
        val text = runCatching { clip.getItemAt(0).coerceToText(this).toString() }
            .getOrNull()
            .orEmpty()
        if (text.isEmpty()) {
            return false
        }
        selectionCandidate = ""
        MobileClipboardSync.enqueue(this, text, "clipboard")
        reportClipboardStatus("listening")
        return true
    }

    private fun sendSelectionCandidate(source: String): Boolean {
        val age = System.currentTimeMillis() - selectionCandidateAt
        if (selectionCandidate.isEmpty() || age !in 0..SELECTION_MAX_AGE_MS) {
            return false
        }
        val text = selectionCandidate
        selectionCandidate = ""
        return MobileClipboardSync.enqueue(this, text, source)
    }

    private fun reportClipboardStatus(status: String) {
        if (status == lastClipboardStatus) {
            return
        }
        lastClipboardStatus = status
        MainActivity.sendClipboardSyncState(status)
    }

    companion object {
        private var instance: VoiceKeyClickAccessibilityService? = null

        fun isRunning(): Boolean = instance != null

        fun refreshClipboardMonitoring() {
            instance?.refreshClipboardMonitoringInternal()
        }

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
            holdStopReason = "released"
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
            if (holdActive) {
                MainActivity.sendVoiceHoldState(true, "started")
            }
            return "ok"
        }

        fun stopHold(reason: String = "released") {
            if (holdActive) {
                holdStopReason = reason
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
                            val reason = if (holdReleaseRequested) holdStopReason else "gesture_cancelled"
                            holdActive = false
                            holdReleaseRequested = false
                            MainActivity.sendVoiceHoldState(false, reason)
                        }
                    }
                },
                mainHandler,
            )
            if (!dispatched) {
                holdActive = false
                holdReleaseRequested = false
                MainActivity.sendVoiceHoldState(false, "gesture_cancelled")
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
                MainActivity.sendVoiceHoldState(false, holdStopReason)
            }
        }

        private const val CALIBRATION_DOT_SIZE_DP = 34
        private const val HOLD_SEGMENT_MS = 500L
        private const val HOLD_RELEASE_MS = 40L
        private const val HOLD_OFFSET_DP = 2f
        private const val HOLD_RELEASE_NUDGE_PX = 0.5f
        private const val CLIPBOARD_READ_DELAY_MS = 90L
        private const val COPY_ACTION_DELAY_MS = 140L
        private const val SELECTION_MAX_AGE_MS = 15_000L
        private const val MAX_SELECTION_CHARS = 50_000
        private const val SENSITIVE_CLIP_KEY = "android.content.extra.IS_SENSITIVE"
        private val mainHandler = Handler(Looper.getMainLooper())
        private var holdActive = false
        private var holdReleaseRequested = false
        private var holdStopReason = "released"
        private var holdGeneration = 0
    }
}
