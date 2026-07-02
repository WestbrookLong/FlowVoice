package com.westbrook.voiceinput.voice_input_mobile

import android.app.Service
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.TextView
import kotlin.math.abs

class VoiceClickCalibrationService : Service() {
    private var windowManager: WindowManager? = null
    private var overlayView: FrameLayout? = null
    private var params: WindowManager.LayoutParams? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                removeOverlay()
                stopSelf()
            }
            ACTION_START, null -> showOverlay()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        removeOverlay()
        super.onDestroy()
    }

    private fun showOverlay() {
        if (!canDrawOverlays()) {
            stopSelf()
            return
        }
        if (overlayView != null) {
            return
        }
        val manager = getSystemService(WINDOW_SERVICE) as WindowManager
        windowManager = manager

        val root = FrameLayout(this)
        root.clipChildren = false
        root.clipToPadding = false

        val label = TextView(this)
        label.text = "\u62D6\u5230\u8BED\u97F3\u952E\u4F4D\u7F6E\uFF0C\u70B9\u51FB\u5173\u95ED"
        label.gravity = Gravity.CENTER
        label.typeface = Typeface.DEFAULT_BOLD
        label.textSize = 11f
        label.setTextColor(Color.rgb(244, 255, 248))
        label.background = roundedRect(Color.rgb(17, 17, 17), Color.rgb(5, 5, 5), 12)
        root.addView(
            label,
            FrameLayout.LayoutParams(dp(172), dp(30)).apply {
                leftMargin = 0
                topMargin = 0
            },
        )

        val dot = TextView(this)
        dot.text = ""
        dot.background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(Color.rgb(40, 245, 141))
            setStroke(dp(3), Color.rgb(5, 5, 5))
        }
        root.addView(
            dot,
            FrameLayout.LayoutParams(dp(34), dp(34)).apply {
                leftMargin = dp(69)
                topMargin = dp(34)
            },
        )

        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val defaultX = (resources.displayMetrics.widthPixels - dp(172)) / 2
        val defaultY = resources.displayMetrics.heightPixels - dp(260)
        params = WindowManager.LayoutParams(
            dp(172),
            dp(70),
            overlayType(),
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = prefs.getFloat(KEY_X, defaultX.toFloat()).toInt()
            y = prefs.getFloat(KEY_Y, defaultY.toFloat()).toInt()
        }

        installDragAndSave(root)
        manager.addView(root, params)
        overlayView = root
        saveCenter()
    }

    private fun installDragAndSave(root: View) {
        var startX = 0
        var startY = 0
        var downRawX = 0f
        var downRawY = 0f
        var dragging = false
        root.setOnTouchListener { _, event ->
            val layoutParams = params ?: return@setOnTouchListener false
            val manager = windowManager ?: return@setOnTouchListener false
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    startX = layoutParams.x
                    startY = layoutParams.y
                    downRawX = event.rawX
                    downRawY = event.rawY
                    dragging = false
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downRawX
                    val dy = event.rawY - downRawY
                    if (abs(dx) > dp(4) || abs(dy) > dp(4)) {
                        dragging = true
                        layoutParams.x = startX + dx.toInt()
                        layoutParams.y = startY + dy.toInt()
                        manager.updateViewLayout(root, layoutParams)
                        saveCenter()
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    saveCenter()
                    if (!dragging) {
                        removeOverlay()
                        stopSelf()
                    }
                    true
                }
                else -> false
            }
        }
    }

    private fun saveCenter() {
        val layoutParams = params ?: return
        val centerX = layoutParams.x + dp(86)
        val centerY = layoutParams.y + dp(51)
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit()
            .putFloat(KEY_X, centerX.toFloat())
            .putFloat(KEY_Y, centerY.toFloat())
            .apply()
    }

    private fun removeOverlay() {
        val root = overlayView
        val manager = windowManager
        if (root != null && manager != null) {
            try {
                manager.removeView(root)
            } catch (_: IllegalArgumentException) {
            }
        }
        overlayView = null
        params = null
    }

    private fun canDrawOverlays(): Boolean {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)
    }

    private fun overlayType(): Int {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }
    }

    private fun roundedRect(fill: Int, stroke: Int, radiusDp: Int): GradientDrawable {
        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(radiusDp).toFloat()
            setColor(fill)
            setStroke(dp(2), stroke)
        }
    }

    private fun dp(value: Int): Int {
        return (value * resources.displayMetrics.density).toInt()
    }

    companion object {
        const val ACTION_START = "com.westbrook.voiceinput.voice_input_mobile.START_VOICE_CLICK_CALIBRATION"
        const val ACTION_STOP = "com.westbrook.voiceinput.voice_input_mobile.STOP_VOICE_CLICK_CALIBRATION"
        const val PREFS_NAME = "flowvoice_native"
        const val KEY_X = "voiceClickX"
        const val KEY_Y = "voiceClickY"
    }
}
