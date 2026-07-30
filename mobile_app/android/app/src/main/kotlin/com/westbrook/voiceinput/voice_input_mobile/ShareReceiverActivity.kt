package com.westbrook.voiceinput.voice_input_mobile

import android.app.Activity
import android.content.Intent
import android.database.Cursor
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.widget.Toast
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileInputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

class ShareReceiverActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uris = sharedUris(intent)
        if (uris.isEmpty()) {
            finishWithMessage("没有可上传的文件")
            return
        }

        Thread {
            var files: List<CachedShare> = emptyList()
            try {
                files = uris.map { cacheSharedUri(it) }
                upload(files)
                runOnUiThread { finishWithMessage("已发送到电脑") }
            } catch (error: Exception) {
                val detail = error.message?.take(120).orEmpty()
                runOnUiThread {
                    finishWithMessage(if (detail.isEmpty()) "发送失败" else "发送失败：$detail")
                }
            } finally {
                files.forEach { it.file.delete() }
            }
        }.start()
    }

    private fun sharedUris(source: Intent): List<Uri> {
        return when (source.action) {
            Intent.ACTION_SEND -> listOfNotNull(parcelableUri(source, Intent.EXTRA_STREAM))
            Intent.ACTION_SEND_MULTIPLE -> parcelableUriList(source, Intent.EXTRA_STREAM)
            else -> emptyList()
        }
    }

    private fun cacheSharedUri(uri: Uri): CachedShare {
        val displayName = queryDisplayName(uri) ?: "shared-${System.currentTimeMillis()}"
        val safeName = displayName.replace(Regex("""[\\/:*?"<>|\u0000-\u001f]"""), "_")
            .take(180)
            .ifBlank { "shared-file" }
        val directory = File(cacheDir, "shared_uploads").apply { mkdirs() }
        val file = File(directory, "${UUID.randomUUID()}-$safeName")
        contentResolver.openInputStream(uri).use { input ->
            requireNotNull(input) { "无法读取分享文件" }
            file.outputStream().buffered().use { output -> input.copyTo(output, 256 * 1024) }
        }
        return CachedShare(
            file = file,
            displayName = safeName,
            mimeType = contentResolver.getType(uri) ?: "application/octet-stream",
        )
    }

    private fun upload(files: List<CachedShare>) {
        val prefs = getSharedPreferences(CONNECTION_PREFS, MODE_PRIVATE)
        val baseUrl = prefs.getString(KEY_BASE_URL, null)?.trimEnd('/')
        val token = prefs.getString(KEY_TOKEN, null)
        require(!baseUrl.isNullOrBlank() && !token.isNullOrBlank()) {
            "请先打开 Flow Voice 扫码连接电脑"
        }

        val encodedToken = Uri.encode(token)
        val candidates = listOf(
            "$baseUrl/api/files/upload?token=$encodedToken",
            "$baseUrl/api/files/upload/?token=$encodedToken",
            "$baseUrl/?token=$encodedToken",
        )
        var lastResult: UploadResult? = null
        for (candidate in candidates) {
            val result = uploadFollowingRedirects(candidate, token, files)
            if (result.status in 200..299) {
                return
            }
            lastResult = result
            if (result.status !in setOf(404, 405)) {
                break
            }
        }
        val result = lastResult ?: throw IllegalStateException("没有可用的上传地址")
        val host = runCatching { URL(result.url).host }.getOrDefault(result.url)
        val detail = result.message?.trim()?.take(100)
        throw IllegalStateException(
            "上传到 $host 失败（HTTP ${result.status}）" +
                if (detail.isNullOrBlank()) "" else "：$detail",
        )
    }

    private fun uploadFollowingRedirects(
        initialUrl: String,
        token: String,
        files: List<CachedShare>,
    ): UploadResult {
        var uploadUrl = initialUrl
        repeat(MAX_REDIRECTS + 1) { redirectCount ->
            val result = uploadOnce(uploadUrl, token, files)
            if (result.status in REDIRECT_STATUS_CODES) {
                val location = result.location
                    ?: throw IllegalStateException("上传地址重定向缺少 Location")
                if (redirectCount >= MAX_REDIRECTS) {
                    throw IllegalStateException("上传地址重定向次数过多")
                }
                uploadUrl = URL(URL(uploadUrl), location).toString()
                return@repeat
            }
            return result
        }
        throw IllegalStateException("上传地址重定向次数过多")
    }

    private fun uploadOnce(
        uploadUrl: String,
        token: String,
        files: List<CachedShare>,
    ): UploadResult {
        val boundary = "----FlowVoice${UUID.randomUUID().toString().replace("-", "")}"
        val connection = (URL(uploadUrl).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 15_000
            readTimeout = 120_000
            doOutput = true
            instanceFollowRedirects = false
            setChunkedStreamingMode(256 * 1024)
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            setRequestProperty("Accept", "application/json")
        }

        try {
            BufferedOutputStream(connection.outputStream, 256 * 1024).use { output ->
                files.forEach { shared ->
                    output.write("--$boundary\r\n".toByteArray())
                    output.write(
                        "Content-Disposition: form-data; name=\"files\"; filename=\"${asciiFilename(shared.displayName)}\"; filename*=UTF-8''${Uri.encode(shared.displayName)}\r\n"
                            .toByteArray(),
                    )
                    output.write("Content-Type: ${shared.mimeType}\r\n\r\n".toByteArray())
                    FileInputStream(shared.file).use { input ->
                        input.copyTo(output, 256 * 1024)
                    }
                    output.write("\r\n".toByteArray())
                }
                output.write("--$boundary--\r\n".toByteArray())
            }
            val status = connection.responseCode
            val message = if (status in 200..299) {
                null
            } else {
                connection.errorStream?.bufferedReader()?.use { it.readText() }
            }
            return UploadResult(
                status = status,
                url = uploadUrl,
                location = connection.getHeaderField("Location"),
                message = message,
            )
        } finally {
            connection.disconnect()
        }
    }

    private fun queryDisplayName(uri: Uri): String? {
        var cursor: Cursor? = null
        return try {
            cursor = contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            if (cursor != null && cursor.moveToFirst()) {
                val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (index >= 0) cursor.getString(index) else null
            } else {
                null
            }
        } finally {
            cursor?.close()
        }
    }

    private fun finishWithMessage(message: String) {
        Toast.makeText(applicationContext, message, Toast.LENGTH_LONG).show()
        finish()
    }

    private fun asciiFilename(value: String): String {
        val fallback = value.map { character ->
            if (character.code in 0x20..0x7e && character !in charArrayOf('\\', '"')) character else '_'
        }.joinToString("")
        return fallback.ifBlank { "shared-file" }
    }

    @Suppress("DEPRECATION")
    private fun parcelableUri(intent: Intent, key: String): Uri? {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(key, Uri::class.java)
        } else {
            intent.getParcelableExtra(key)
        }
    }

    @Suppress("DEPRECATION")
    private fun parcelableUriList(intent: Intent, key: String): List<Uri> {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableArrayListExtra(key, Uri::class.java).orEmpty()
        } else {
            intent.getParcelableArrayListExtra<Uri>(key).orEmpty()
        }
    }

    private data class CachedShare(
        val file: File,
        val displayName: String,
        val mimeType: String,
    )

    private data class UploadResult(
        val status: Int,
        val url: String,
        val location: String?,
        val message: String?,
    )

    companion object {
        private const val MAX_REDIRECTS = 5
        private val REDIRECT_STATUS_CODES = setOf(301, 302, 303, 307, 308)
        const val CONNECTION_PREFS = "flowvoice_share_connection"
        const val KEY_BASE_URL = "base_url"
        const val KEY_TOKEN = "token"
    }
}
