package com.westbrook.voiceinput.voice_input_mobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    override fun onResume() {
        super.onResume()
        stopFloatingOverlay()
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
                "hasRecordAudioPermission" -> result.success(hasRecordAudioPermission())
                "requestRecordAudioPermission" -> {
                    requestRecordAudioPermission()
                    result.success(null)
                }
                "startBuiltInVoice" -> {
                    BuiltInVoiceEngine.start(
                        context = applicationContext,
                        onText = { text, isFinal -> sendBuiltInVoiceText(text, isFinal) },
                        onStatus = { status -> sendBuiltInVoiceStatus(status) },
                    )
                    result.success(true)
                }
                "stopBuiltInVoice" -> {
                    BuiltInVoiceEngine.stop()
                    result.success(null)
                }
                "startOverlay" -> {
                    val text = call.argument<String>("text").orEmpty()
                    val connected = call.argument<Boolean>("connected") ?: false
                    val builtInVoice = call.argument<Boolean>("builtInVoice") ?: false
                    if (!canDrawOverlays()) {
                        result.success(false)
                        return@setMethodCallHandler
                    }
                    val intent = Intent(this, FloatingInputService::class.java)
                        .setAction(FloatingInputService.ACTION_START)
                        .putExtra(FloatingInputService.EXTRA_TEXT, text)
                        .putExtra(FloatingInputService.EXTRA_CONNECTED, connected)
                        .putExtra(FloatingInputService.EXTRA_BUILT_IN_VOICE, builtInVoice)
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
                else -> result.notImplemented()
            }
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

    private fun hasRecordAudioPermission(): Boolean {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M ||
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    }

    private fun requestRecordAudioPermission() {
        if (hasRecordAudioPermission()) {
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 2401)
        }
    }

    private fun stopFloatingOverlay() {
        val intent = Intent(this, FloatingInputService::class.java)
            .setAction(FloatingInputService.ACTION_STOP)
        startService(intent)
    }

    companion object {
        private const val OVERLAY_CHANNEL = "flowvoice/overlay"
        private var overlayChannel: MethodChannel? = null

        fun sendOverlayText(text: String) {
            overlayChannel?.invokeMethod("overlayTextChanged", text)
        }

        fun sendBuiltInVoiceText(text: String, isFinal: Boolean) {
            overlayChannel?.invokeMethod(
                "builtInVoiceText",
                mapOf("text" to text, "final" to isFinal),
            )
        }

        fun sendBuiltInVoiceStatus(status: String) {
            overlayChannel?.invokeMethod("builtInVoiceStatus", status)
        }
    }
}
