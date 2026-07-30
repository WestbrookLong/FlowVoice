package com.westbrook.voiceinput.voice_input_mobile

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ContentUris
import android.content.Intent
import android.content.pm.PackageManager
import android.database.ContentObserver
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.provider.MediaStore
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import java.util.concurrent.Executors

class ScreenshotMonitorService : Service() {
    private val uploadExecutor = Executors.newSingleThreadExecutor()
    private lateinit var observerThread: HandlerThread
    private lateinit var observerHandler: Handler
    private var observer: ContentObserver? = null
    private var startedAtSeconds = 0L
    private val pendingUris = mutableSetOf<String>()

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification())
        startedAtSeconds = System.currentTimeMillis() / 1000L
        observerThread = HandlerThread("FlowVoiceScreenshotObserver").apply { start() }
        observerHandler = Handler(observerThread.looper)
        registerObserver()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP || !isEnabled(this)) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (!hasImagePermission(this)) {
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onDestroy() {
        observer?.let { contentResolver.unregisterContentObserver(it) }
        observer = null
        uploadExecutor.shutdownNow()
        if (::observerThread.isInitialized) {
            observerThread.quitSafely()
        }
        super.onDestroy()
    }

    private fun registerObserver() {
        if (!hasImagePermission(this)) {
            return
        }
        observer = object : ContentObserver(observerHandler) {
            override fun onChange(selfChange: Boolean, uri: Uri?) {
                observerHandler.removeCallbacks(scanLatestRunnable)
                observerHandler.postDelayed(scanLatestRunnable, FILE_SETTLE_DELAY_MS)
            }
        }.also {
            contentResolver.registerContentObserver(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                true,
                it,
            )
        }
    }

    private val scanLatestRunnable = Runnable {
        runCatching { findRecentScreenshots() }
    }

    private fun findRecentScreenshots() {
        val projection = mutableListOf(
            MediaStore.Images.Media._ID,
            MediaStore.Images.Media.DISPLAY_NAME,
            MediaStore.Images.Media.MIME_TYPE,
            MediaStore.Images.Media.DATE_ADDED,
            MediaStore.Images.Media.SIZE,
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            projection += MediaStore.Images.Media.RELATIVE_PATH
            projection += MediaStore.Images.Media.IS_PENDING
        }
        val cursor = contentResolver.query(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            projection.toTypedArray(),
            "${MediaStore.Images.Media.DATE_ADDED}>=?",
            arrayOf((startedAtSeconds - 3L).toString()),
            "${MediaStore.Images.Media.DATE_ADDED} DESC",
        ) ?: return

        cursor.use {
            val idIndex = it.getColumnIndexOrThrow(MediaStore.Images.Media._ID)
            val nameIndex = it.getColumnIndexOrThrow(MediaStore.Images.Media.DISPLAY_NAME)
            val mimeIndex = it.getColumnIndexOrThrow(MediaStore.Images.Media.MIME_TYPE)
            val sizeIndex = it.getColumnIndexOrThrow(MediaStore.Images.Media.SIZE)
            val pathIndex = it.getColumnIndex(MediaStore.Images.Media.RELATIVE_PATH)
            val pendingIndex = it.getColumnIndex(MediaStore.Images.Media.IS_PENDING)
            while (it.moveToNext()) {
                if (pendingIndex >= 0 && it.getInt(pendingIndex) != 0) {
                    continue
                }
                val id = it.getLong(idIndex)
                val name = it.getString(nameIndex).orEmpty()
                val mimeType = it.getString(mimeIndex) ?: "image/png"
                val path = if (pathIndex >= 0) it.getString(pathIndex).orEmpty() else ""
                val size = it.getLong(sizeIndex)
                if (size <= 0L || !looksLikeScreenshot(name, path)) {
                    continue
                }
                val uri = ContentUris.withAppendedId(
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                    id,
                )
                enqueueUpload(uri, name, mimeType)
            }
        }
    }

    private fun looksLikeScreenshot(name: String, relativePath: String): Boolean {
        val value = "$relativePath/$name".lowercase()
        return SCREENSHOT_MARKERS.any { value.contains(it) }
    }

    private fun enqueueUpload(uri: Uri, displayName: String, mimeType: String) {
        val key = uri.toString()
        val history = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .getStringSet(KEY_UPLOADED_URIS, emptySet())
            .orEmpty()
        synchronized(pendingUris) {
            if (key in pendingUris || key in history) {
                return
            }
            pendingUris += key
        }

        uploadExecutor.execute {
            var cached: FlowUploadFile? = null
            try {
                cached = FlowFileUploader.cacheUri(
                    this,
                    uri,
                    displayName.ifBlank { "screenshot-${System.currentTimeMillis()}.png" },
                    mimeType,
                    "automatic_screenshots",
                )
                var lastError: Exception? = null
                for (delayMs in RETRY_DELAYS_MS) {
                    if (delayMs > 0L) {
                        Thread.sleep(delayMs)
                    }
                    try {
                        FlowFileUploader.upload(this, listOf(cached))
                        rememberUploaded(key)
                        lastError = null
                        break
                    } catch (error: Exception) {
                        lastError = error
                    }
                }
                lastError?.let { throw it }
            } catch (_: Exception) {
                // A future MediaStore change can retry this item.
            } finally {
                cached?.file?.delete()
                synchronized(pendingUris) { pendingUris -= key }
            }
        }
    }

    private fun rememberUploaded(uri: String) {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val recent = prefs.getStringSet(KEY_UPLOADED_URIS, emptySet())
            .orEmpty()
            .toMutableList()
        recent.remove(uri)
        recent.add(uri)
        prefs.edit()
            .putStringSet(KEY_UPLOADED_URIS, recent.takeLast(MAX_HISTORY).toSet())
            .apply()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "Screenshot upload",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Keeps Flow Voice ready to upload new screenshots."
                setShowBadge(false)
            },
        )
    }

    private fun buildNotification() = NotificationCompat.Builder(this, CHANNEL_ID)
        .setSmallIcon(R.mipmap.ic_launcher)
        .setContentTitle("Flow Voice")
        .setContentText("Automatic screenshot upload is active")
        .setOngoing(true)
        .setSilent(true)
        .setContentIntent(
            PendingIntent.getActivity(
                this,
                0,
                Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            ),
        )
        .build()

    companion object {
        const val ACTION_START =
            "com.westbrook.voiceinput.voice_input_mobile.START_SCREENSHOT_MONITOR"
        const val ACTION_STOP =
            "com.westbrook.voiceinput.voice_input_mobile.STOP_SCREENSHOT_MONITOR"
        const val PREFS_NAME = "flowvoice_screenshot_monitor"
        const val KEY_ENABLED = "enabled"
        private const val KEY_UPLOADED_URIS = "uploaded_uris"
        private const val CHANNEL_ID = "flowvoice_screenshot_upload"
        private const val NOTIFICATION_ID = 4207
        private const val FILE_SETTLE_DELAY_MS = 900L
        private const val MAX_HISTORY = 200
        private val RETRY_DELAYS_MS = longArrayOf(0L, 1_500L, 4_000L)
        private val SCREENSHOT_MARKERS = listOf(
            "screenshot",
            "screen_shot",
            "screen-shot",
            "screencapture",
            "screen_capture",
            "\u622a\u56fe",
            "\u622a\u5c4f",
        )

        fun isEnabled(context: android.content.Context): Boolean {
            return context.getSharedPreferences(PREFS_NAME, android.content.Context.MODE_PRIVATE)
                .getBoolean(KEY_ENABLED, false)
        }

        fun setEnabled(context: android.content.Context, enabled: Boolean) {
            context.getSharedPreferences(PREFS_NAME, android.content.Context.MODE_PRIVATE)
                .edit()
                .putBoolean(KEY_ENABLED, enabled)
                .apply()
        }

        fun hasImagePermission(context: android.content.Context): Boolean {
            val permission = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                Manifest.permission.READ_MEDIA_IMAGES
            } else {
                Manifest.permission.READ_EXTERNAL_STORAGE
            }
            return ContextCompat.checkSelfPermission(context, permission) ==
                PackageManager.PERMISSION_GRANTED
        }
    }
}
