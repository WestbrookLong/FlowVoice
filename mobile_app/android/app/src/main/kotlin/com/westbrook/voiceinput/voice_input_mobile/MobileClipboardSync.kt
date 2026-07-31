package com.westbrook.voiceinput.voice_input_mobile

import android.content.Context
import android.net.Uri
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

object MobileClipboardSync {
    private val executor = Executors.newSingleThreadExecutor()
    private val lock = Any()
    private var lastText = ""
    private var lastSentAt = 0L

    fun isEnabled(context: Context): Boolean {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getBoolean(KEY_ENABLED, false)
    }

    fun setEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_ENABLED, enabled)
            .apply()
    }

    fun enqueue(context: Context, value: String, source: String): Boolean {
        if (!isEnabled(context)) {
            return false
        }
        val text = value.take(MAX_TEXT_CHARS)
        if (text.isEmpty()) {
            return false
        }
        val now = System.currentTimeMillis()
        synchronized(lock) {
            if (text == lastText && now - lastSentAt < DEDUPE_WINDOW_MS) {
                return false
            }
            lastText = text
            lastSentAt = now
        }
        val appContext = context.applicationContext
        executor.execute {
            for (delayMs in RETRY_DELAYS_MS) {
                if (delayMs > 0L) {
                    Thread.sleep(delayMs)
                }
                if (runCatching { send(appContext, text, source) }.isSuccess) {
                    break
                }
            }
        }
        return true
    }

    private fun send(context: Context, text: String, source: String) {
        val prefs = context.getSharedPreferences(
            ShareReceiverActivity.CONNECTION_PREFS,
            Context.MODE_PRIVATE,
        )
        val baseUrl = prefs.getString(ShareReceiverActivity.KEY_BASE_URL, null)?.trimEnd('/')
        val token = prefs.getString(ShareReceiverActivity.KEY_TOKEN, null)
        require(!baseUrl.isNullOrBlank() && !token.isNullOrBlank()) {
            "No paired computer is available."
        }

        val payload = JSONObject()
            .put("text", text)
            .put("source", source)
            .toString()
            .toByteArray(Charsets.UTF_8)
        val encodedToken = Uri.encode(token)
        val candidates = listOf(
            "$baseUrl/api/clipboard?token=$encodedToken",
            "$baseUrl/api/clipboard/?token=$encodedToken",
        )
        var lastStatus = 0
        for (candidate in candidates) {
            val status = postFollowingRedirects(candidate, token, payload)
            if (status in 200..299) {
                return
            }
            lastStatus = status
            if (status !in setOf(404, 405)) {
                break
            }
        }
        throw IllegalStateException("Clipboard sync failed (HTTP $lastStatus).")
    }

    private fun postFollowingRedirects(
        initialUrl: String,
        token: String,
        payload: ByteArray,
    ): Int {
        var requestUrl = initialUrl
        repeat(MAX_REDIRECTS + 1) { redirectCount ->
            val connection = (URL(requestUrl).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 10_000
                readTimeout = 20_000
                doOutput = true
                instanceFollowRedirects = false
                setFixedLengthStreamingMode(payload.size)
                setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
                setRequestProperty("Accept", "application/json")
            }
            try {
                connection.outputStream.use { it.write(payload) }
                val status = connection.responseCode
                if (status in REDIRECT_STATUS_CODES) {
                    val location = connection.getHeaderField("Location")
                        ?: throw IllegalStateException("Clipboard redirect has no Location header.")
                    if (redirectCount >= MAX_REDIRECTS) {
                        throw IllegalStateException("Too many clipboard redirects.")
                    }
                    requestUrl = URL(URL(requestUrl), location).toString()
                    return@repeat
                }
                if (status !in 200..299) {
                    connection.errorStream?.close()
                } else {
                    connection.inputStream?.close()
                }
                return status
            } finally {
                connection.disconnect()
            }
        }
        throw IllegalStateException("Too many clipboard redirects.")
    }

    private const val PREFS_NAME = "flowvoice_mobile_clipboard"
    private const val KEY_ENABLED = "enabled"
    private const val MAX_TEXT_CHARS = 50_000
    private const val DEDUPE_WINDOW_MS = 1_000L
    private const val MAX_REDIRECTS = 5
    private val RETRY_DELAYS_MS = longArrayOf(0L, 800L, 2_500L)
    private val REDIRECT_STATUS_CODES = setOf(301, 302, 303, 307, 308)
}
