package com.westbrook.voiceinput.voice_input_mobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.ContextCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    override fun onResume() {
        super.onResume()
        stopFloatingOverlay()
        if (
            ScreenshotMonitorService.isEnabled(this) &&
            ScreenshotMonitorService.hasImagePermission(this)
        ) {
            startScreenshotMonitor()
            sendScreenshotUploadState("listening")
        }
        if (MobileClipboardSync.isEnabled(this)) {
            VoiceKeyClickAccessibilityService.refreshClipboardMonitoring()
            sendClipboardSyncState(
                if (VoiceKeyClickAccessibilityService.isRunning()) {
                    "listening"
                } else {
                    "accessibility_required"
                },
            )
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        overlayChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, OVERLAY_CHANNEL)
        overlayChannel?.setMethodCallHandler { call, result ->
            when (call.method) {
                "hasOverlayPermission" -> result.success(canDrawOverlays())
                "requestOverlayPermission" -> {
                    requestOverlayPermission()
                    result.success(null)
                }
                "isVoiceClickAccessibilityEnabled" -> {
                    result.success(VoiceKeyClickAccessibilityService.isEnabled(this))
                }
                "openAccessibilitySettings" -> {
                    openAccessibilitySettings()
                    result.success(null)
                }
                "startVoiceClickCalibration" -> {
                    if (!canDrawOverlays()) {
                        result.success(false)
                        return@setMethodCallHandler
                    }
                    val intent = Intent(this, VoiceClickCalibrationService::class.java)
                        .setAction(VoiceClickCalibrationService.ACTION_START)
                    startService(intent)
                    result.success(true)
                }
                "startVoiceHold" -> {
                    val overlayResult = FloatingInputService.startRemoteVoiceHoldIfRunning()
                    result.success(
                        overlayResult
                            ?: VoiceKeyClickAccessibilityService.startHold(applicationContext),
                    )
                }
                "stopVoiceHold" -> {
                    if (!FloatingInputService.stopRemoteVoiceHoldIfRunning()) {
                        VoiceKeyClickAccessibilityService.stopHold()
                    }
                    result.success(null)
                }
                "startOverlay" -> {
                    val text = call.argument<String>("text").orEmpty()
                    val connected = call.argument<Boolean>("connected") ?: false
                    val autoVoiceClick = call.argument<Boolean>("autoVoiceClick") ?: false
                    val autoVoiceClickDelayMs =
                        call.argument<Int>("autoVoiceClickDelayMs") ?: 500
                    val autoVoiceClickDurationMs =
                        call.argument<Int>("autoVoiceClickDurationMs") ?: 500
                    if (!canDrawOverlays()) {
                        result.success(false)
                        return@setMethodCallHandler
                    }
                    val intent = Intent(this, FloatingInputService::class.java)
                        .setAction(FloatingInputService.ACTION_START)
                        .putExtra(FloatingInputService.EXTRA_TEXT, text)
                        .putExtra(FloatingInputService.EXTRA_CONNECTED, connected)
                        .putExtra(FloatingInputService.EXTRA_AUTO_VOICE_CLICK, autoVoiceClick)
                        .putExtra(
                            FloatingInputService.EXTRA_AUTO_VOICE_CLICK_DELAY_MS,
                            autoVoiceClickDelayMs,
                        )
                        .putExtra(
                            FloatingInputService.EXTRA_AUTO_VOICE_CLICK_DURATION_MS,
                            autoVoiceClickDurationMs,
                        )
                    startService(intent)
                    result.success(true)
                }
                "stopOverlay" -> {
                    stopFloatingOverlay()
                    result.success(null)
                }
                "sendToBackground" -> {
                    moveTaskToBack(true)
                    result.success(null)
                }
                "updateShareConnection" -> {
                    val baseUrl = call.argument<String>("baseUrl").orEmpty().trimEnd('/')
                    val token = call.argument<String>("token").orEmpty()
                    if (baseUrl.isBlank() || token.isBlank()) {
                        result.success(false)
                    } else {
                        val saved = getSharedPreferences(
                            ShareReceiverActivity.CONNECTION_PREFS,
                            MODE_PRIVATE,
                        ).edit()
                            .putString(ShareReceiverActivity.KEY_BASE_URL, baseUrl)
                            .putString(ShareReceiverActivity.KEY_TOKEN, token)
                            .commit()
                        result.success(saved)
                    }
                }
                "setAutoScreenshotUpload" -> {
                    val enabled = call.argument<Boolean>("enabled") ?: false
                    result.success(applyScreenshotUploadConfig(enabled))
                }
                "setAutoClipboardSync" -> {
                    val enabled = call.argument<Boolean>("enabled") ?: false
                    MobileClipboardSync.setEnabled(this, enabled)
                    VoiceKeyClickAccessibilityService.refreshClipboardMonitoring()
                    result.success(
                        if (!enabled) {
                            "disabled"
                        } else if (VoiceKeyClickAccessibilityService.isRunning()) {
                            "listening"
                        } else {
                            "accessibility_required"
                        },
                    )
                }
                else -> result.notImplemented()
            }
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != SCREENSHOT_PERMISSION_REQUEST) {
            return
        }
        if (
            grantResults.isNotEmpty() &&
            grantResults[0] == PackageManager.PERMISSION_GRANTED
        ) {
            startScreenshotMonitor()
            sendScreenshotUploadState("listening")
        } else {
            sendScreenshotUploadState("permission_required")
        }
    }

    override fun cleanUpFlutterEngine(flutterEngine: FlutterEngine) {
        overlayChannel?.setMethodCallHandler(null)
        overlayChannel = null
        super.cleanUpFlutterEngine(flutterEngine)
    }

    private fun canDrawOverlays(): Boolean {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)
    }

    private fun requestOverlayPermission() {
        if (canDrawOverlays() || Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            return
        }
        val intent = Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:$packageName"),
        )
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivity(intent)
    }

    private fun openAccessibilitySettings() {
        val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivity(intent)
    }

    private fun applyScreenshotUploadConfig(enabled: Boolean): String {
        ScreenshotMonitorService.setEnabled(this, enabled)
        if (!enabled) {
            stopScreenshotMonitor()
            return "disabled"
        }
        if (!ScreenshotMonitorService.hasImagePermission(this)) {
            requestScreenshotPermission()
            return "permission_required"
        }
        startScreenshotMonitor()
        return "listening"
    }

    private fun requestScreenshotPermission() {
        val permission = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            Manifest.permission.READ_MEDIA_IMAGES
        } else {
            Manifest.permission.READ_EXTERNAL_STORAGE
        }
        requestPermissions(arrayOf(permission), SCREENSHOT_PERMISSION_REQUEST)
    }

    private fun startScreenshotMonitor() {
        val intent = Intent(this, ScreenshotMonitorService::class.java)
            .setAction(ScreenshotMonitorService.ACTION_START)
        ContextCompat.startForegroundService(this, intent)
    }

    private fun stopScreenshotMonitor() {
        val intent = Intent(this, ScreenshotMonitorService::class.java)
            .setAction(ScreenshotMonitorService.ACTION_STOP)
        startService(intent)
    }

    private fun stopFloatingOverlay() {
        val intent = Intent(this, FloatingInputService::class.java)
            .setAction(FloatingInputService.ACTION_STOP)
        startService(intent)
    }

    companion object {
        private const val OVERLAY_CHANNEL = "flowvoice/overlay"
        private const val SCREENSHOT_PERMISSION_REQUEST = 2402
        private var overlayChannel: MethodChannel? = null

        fun sendOverlayText(text: String) {
            overlayChannel?.invokeMethod("overlayTextChanged", text)
        }

        fun sendOverlayDiagnostic(message: String) {
            overlayChannel?.invokeMethod("overlayDiagnostic", message)
        }

        fun sendVoiceHoldState(active: Boolean, reason: String) {
            overlayChannel?.invokeMethod(
                "voiceHoldState",
                mapOf("active" to active, "reason" to reason),
            )
        }

        fun sendScreenshotUploadState(status: String) {
            overlayChannel?.invokeMethod("screenshotUploadState", status)
        }

        fun sendClipboardSyncState(status: String) {
            overlayChannel?.invokeMethod("clipboardSyncState", status)
        }
    }
}
