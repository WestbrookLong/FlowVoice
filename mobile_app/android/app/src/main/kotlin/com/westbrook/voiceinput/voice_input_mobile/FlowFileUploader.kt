package com.westbrook.voiceinput.voice_input_mobile

import android.content.Context
import android.net.Uri
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileInputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

data class FlowUploadFile(
    val file: File,
    val displayName: String,
    val mimeType: String,
)

object FlowFileUploader {
    fun cacheUri(
        context: Context,
        uri: Uri,
        displayName: String,
        mimeType: String,
        cacheFolder: String,
    ): FlowUploadFile {
        val safeName = displayName.replace(Regex("""[\\/:*?"<>|\u0000-\u001f]"""), "_")
            .take(180)
            .ifBlank { "shared-file" }
        val directory = File(context.cacheDir, cacheFolder).apply { mkdirs() }
        val file = File(directory, "${UUID.randomUUID()}-$safeName")
        context.contentResolver.openInputStream(uri).use { input ->
            requireNotNull(input) { "Unable to read the selected file." }
            file.outputStream().buffered().use { output ->
                input.copyTo(output, 256 * 1024)
            }
        }
        return FlowUploadFile(file, safeName, mimeType)
    }

    fun upload(context: Context, files: List<FlowUploadFile>) {
        val prefs = context.getSharedPreferences(
            ShareReceiverActivity.CONNECTION_PREFS,
            Context.MODE_PRIVATE,
        )
        val baseUrl = prefs.getString(ShareReceiverActivity.KEY_BASE_URL, null)?.trimEnd('/')
        val token = prefs.getString(ShareReceiverActivity.KEY_TOKEN, null)
        require(!baseUrl.isNullOrBlank() && !token.isNullOrBlank()) {
            "Open Flow Voice and connect it to the computer first."
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
        val result = lastResult ?: throw IllegalStateException("No upload endpoint is available.")
        val host = runCatching { URL(result.url).host }.getOrDefault(result.url)
        val detail = result.message?.trim()?.take(100)
        throw IllegalStateException(
            "Upload to $host failed (HTTP ${result.status})" +
                if (detail.isNullOrBlank()) "" else ": $detail",
        )
    }

    private fun uploadFollowingRedirects(
        initialUrl: String,
        token: String,
        files: List<FlowUploadFile>,
    ): UploadResult {
        var uploadUrl = initialUrl
        repeat(MAX_REDIRECTS + 1) { redirectCount ->
            val result = uploadOnce(uploadUrl, token, files)
            if (result.status in REDIRECT_STATUS_CODES) {
                val location = result.location
                    ?: throw IllegalStateException("Upload redirect has no Location header.")
                if (redirectCount >= MAX_REDIRECTS) {
                    throw IllegalStateException("Too many upload redirects.")
                }
                uploadUrl = URL(URL(uploadUrl), location).toString()
                return@repeat
            }
            return result
        }
        throw IllegalStateException("Too many upload redirects.")
    }

    private fun uploadOnce(
        uploadUrl: String,
        token: String,
        files: List<FlowUploadFile>,
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
            return UploadResult(
                status = status,
                url = uploadUrl,
                location = connection.getHeaderField("Location"),
                message = if (status in 200..299) {
                    null
                } else {
                    connection.errorStream?.bufferedReader()?.use { it.readText() }
                },
            )
        } finally {
            connection.disconnect()
        }
    }

    private fun asciiFilename(value: String): String {
        val fallback = value.map { character ->
            if (character.code in 0x20..0x7e && character !in charArrayOf('\\', '"')) {
                character
            } else {
                '_'
            }
        }.joinToString("")
        return fallback.ifBlank { "shared-file" }
    }

    private data class UploadResult(
        val status: Int,
        val url: String,
        val location: String?,
        val message: String?,
    )

    private const val MAX_REDIRECTS = 5
    private val REDIRECT_STATUS_CODES = setOf(301, 302, 303, 307, 308)
}
