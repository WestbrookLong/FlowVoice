package com.westbrook.voiceinput.voice_input_mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.TextView
import kotlin.math.abs

class FloatingInputService : Service() {
    private var windowManager: WindowManager? = null
    private var overlayView: FrameLayout? = null
    private var hiddenInput: EditText? = null
    private var params: WindowManager.LayoutParams? = null
    private var suppressTextCallback = false
    private var builtInVoiceMode = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                removeOverlay()
                BuiltInVoiceEngine.stop()
                stopSelf()
            }
            ACTION_START, null -> {
                val text = intent?.getStringExtra(EXTRA_TEXT).orEmpty()
                val connected = intent?.getBooleanExtra(EXTRA_CONNECTED, false) ?: false
                val builtInVoice = intent?.getBooleanExtra(EXTRA_BUILT_IN_VOICE, false) ?: false
                showOverlay(text, connected, builtInVoice)
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        removeOverlay()
        BuiltInVoiceEngine.stop()
        super.onDestroy()
    }

    private fun canDrawOverlays(): Boolean {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)
    }

    private fun showOverlay(text: String, connected: Boolean, builtInVoice: Boolean) {
        if (!canDrawOverlays()) {
            stopSelf()
            return
        }
        if (overlayView == null || builtInVoiceMode != builtInVoice) {
            removeOverlay()
            builtInVoiceMode = builtInVoice
            if (builtInVoice) {
                createVoiceOverlay(connected)
                startForeground(NOTIFICATION_ID, createNotification())
                startBuiltInVoice()
            } else {
                createOverlay(connected)
            }
        } else {
            updateIndicator(connected)
        }
        if (builtInVoice) {
            return
        }
        val input = hiddenInput ?: return
        if (input.text.toString() != text) {
            suppressTextCallback = true
            input.setText(text)
            input.setSelection(input.text.length)
            suppressTextCallback = false
        }
    }

    private fun createVoiceOverlay(connected: Boolean) {
        val manager = getSystemService(WINDOW_SERVICE) as WindowManager
        windowManager = manager

        val root = FrameLayout(this)
        root.clipChildren = false
        root.clipToPadding = false

        val dock = FrameLayout(this)
        dock.background = buttonShape(
            fill = Color.rgb(17, 17, 17),
            stroke = Color.rgb(17, 17, 17),
        )
        root.addView(
            dock,
            FrameLayout.LayoutParams(dp(82), dp(42)).apply {
                leftMargin = dp(3)
                topMargin = dp(3)
            },
        )

        val micButton = TextView(this)
        micButton.text = "●"
        micButton.gravity = Gravity.CENTER
        micButton.typeface = Typeface.DEFAULT_BOLD
        micButton.textSize = 21f
        micButton.setTextColor(Color.WHITE)
        micButton.background = buttonDrawable(connected)
        dock.addView(
            micButton,
            FrameLayout.LayoutParams(dp(34), dp(34), Gravity.CENTER).apply {
                leftMargin = dp(4)
            },
        )

        val status = TextView(this)
        status.text = "REC"
        status.gravity = Gravity.CENTER
        status.typeface = Typeface.DEFAULT_BOLD
        status.textSize = 12f
        status.setTextColor(Color.rgb(244, 255, 248))
        dock.addView(
            status,
            FrameLayout.LayoutParams(dp(38), dp(34), Gravity.CENTER or Gravity.END).apply {
                rightMargin = dp(4)
            },
        )

        params = WindowManager.LayoutParams(
            dp(88),
            dp(48),
            overlayType(),
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = dp(18)
            y = dp(160)
        }

        installVoiceDragAndToggle(root)
        manager.addView(root, params)
        overlayView = root
    }

    private fun createOverlay(connected: Boolean) {
        val manager = getSystemService(WINDOW_SERVICE) as WindowManager
        windowManager = manager

        val root = FrameLayout(this)
        root.clipChildren = false
        root.clipToPadding = false

        val shadow = View(this)
        shadow.background = buttonShape(
            fill = Color.rgb(17, 17, 17),
            stroke = Color.rgb(17, 17, 17),
        )
        val buttonSize = dp(33)
        val shadowParams = FrameLayout.LayoutParams(buttonSize, buttonSize).apply {
            leftMargin = dp(4)
            topMargin = dp(4)
        }
        root.addView(shadow, shadowParams)

        val button = TextView(this)
        button.text = ""
        button.gravity = Gravity.CENTER
        button.typeface = Typeface.DEFAULT_BOLD
        button.textSize = 1f
        button.setTextColor(Color.rgb(5, 5, 5))
        button.background = buttonDrawable(connected)

        root.addView(
            button,
            FrameLayout.LayoutParams(buttonSize, buttonSize).apply {
                leftMargin = 0
                topMargin = 0
            },
        )

        val input = EditText(this)
        input.alpha = 0.01f
        input.setTextColor(Color.TRANSPARENT)
        input.setBackgroundColor(Color.TRANSPARENT)
        input.isSingleLine = false
        input.minLines = 1
        input.maxLines = 6
        input.includeFontPadding = false
        input.textSize = 1f
        input.setPadding(0, 0, 0, 0)
        input.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                if (!suppressTextCallback) {
                    MainActivity.sendOverlayText(s?.toString().orEmpty())
                }
            }
            override fun afterTextChanged(s: Editable?) = Unit
        })
        hiddenInput = input
        root.addView(input, FrameLayout.LayoutParams(dp(2), dp(2), Gravity.CENTER))

        params = WindowManager.LayoutParams(
            dp(39),
            dp(39),
            overlayType(),
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = dp(18)
            y = dp(160)
            softInputMode = WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE
        }

        installDragAndFocus(root, input)
        manager.addView(root, params)
        overlayView = root
    }

    private fun installDragAndFocus(root: View, input: EditText) {
        var startX = 0
        var startY = 0
        var downRawX = 0f
        var downRawY = 0f
        var dragging = false

        root.setOnTouchListener { _, event ->
            val layoutParams = params ?: return@setOnTouchListener false
            val manager = windowManager ?: return@setOnTouchListener false
            when (event.actionMasked) {
                MotionEvent.ACTION_OUTSIDE -> {
                    releaseInputFocus(input)
                    true
                }
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
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (!dragging) {
                        focusInput(input)
                    }
                    true
                }
                else -> false
            }
        }
    }

    private fun installVoiceDragAndToggle(root: View) {
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
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (!dragging) {
                        if (BuiltInVoiceEngine.isRecording()) {
                            BuiltInVoiceEngine.stop()
                            MainActivity.sendBuiltInVoiceStatus("stopped")
                        } else {
                            startBuiltInVoice()
                        }
                    }
                    true
                }
                else -> false
            }
        }
    }

    private fun startBuiltInVoice() {
        BuiltInVoiceEngine.start(
            context = applicationContext,
            onText = { text, isFinal -> MainActivity.sendBuiltInVoiceText(text, isFinal) },
            onStatus = { status -> MainActivity.sendBuiltInVoiceStatus(status) },
        )
    }

    private fun focusInput(input: EditText) {
        setOverlayFocusable(true)
        input.isFocusable = true
        input.isFocusableInTouchMode = true
        input.requestFocus()
        input.postDelayed({
            val imm = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
            imm.showSoftInput(input, InputMethodManager.SHOW_IMPLICIT)
        }, 80)
    }

    private fun releaseInputFocus(input: EditText) {
        input.clearFocus()
        val imm = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(input.windowToken, 0)
        setOverlayFocusable(false)
    }

    private fun setOverlayFocusable(focusable: Boolean) {
        val root = overlayView ?: return
        val layoutParams = params ?: return
        val manager = windowManager ?: return
        val notFocusable = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
        layoutParams.flags = if (focusable) {
            layoutParams.flags and notFocusable.inv()
        } else {
            layoutParams.flags or notFocusable
        }
        manager.updateViewLayout(root, layoutParams)
    }

    private fun updateIndicator(connected: Boolean) {
        val root = overlayView ?: return
        val button = root.getChildAt(1) as? TextView ?: return
        button.background = buttonDrawable(connected)
    }

    private fun removeOverlay() {
        val root = overlayView
        val manager = windowManager
        if (root != null && manager != null) {
            try {
                manager.removeView(root)
            } catch (_: IllegalArgumentException) {
                // The system may have already detached the overlay.
            }
        }
        hiddenInput = null
        overlayView = null
        params = null
        if (builtInVoiceMode) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                stopForeground(STOP_FOREGROUND_REMOVE)
            } else {
                @Suppress("DEPRECATION")
                stopForeground(true)
            }
        }
    }

    private fun overlayType(): Int {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }
    }

    private fun buttonDrawable(connected: Boolean): GradientDrawable {
        return buttonShape(
            fill = if (connected) Color.rgb(244, 255, 248) else Color.WHITE,
            stroke = Color.rgb(17, 17, 17),
        )
    }

    private fun buttonShape(fill: Int, stroke: Int): GradientDrawable {
        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(9).toFloat()
            setColor(fill)
            setStroke(dp(2), stroke)
        }
    }

    private fun createNotification(): Notification {
        val channelId = "flowvoice_builtin_voice"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                channelId,
                "Flow Voice recording",
                NotificationManager.IMPORTANCE_LOW,
            )
            getSystemService(NotificationManager::class.java)
                ?.createNotificationChannel(channel)
        }
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, channelId)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setSmallIcon(applicationInfo.icon)
            .setContentTitle("Flow Voice")
            .setContentText("Built-in voice input is listening")
            .setOngoing(true)
            .build()
    }

    private fun dp(value: Int): Int {
        return (value * resources.displayMetrics.density).toInt()
    }

    companion object {
        const val ACTION_START = "com.westbrook.voiceinput.voice_input_mobile.START_FLOATING_INPUT"
        const val ACTION_STOP = "com.westbrook.voiceinput.voice_input_mobile.STOP_FLOATING_INPUT"
        const val EXTRA_TEXT = "text"
        const val EXTRA_CONNECTED = "connected"
        const val EXTRA_BUILT_IN_VOICE = "builtInVoice"
        private const val NOTIFICATION_ID = 4108
    }
}
