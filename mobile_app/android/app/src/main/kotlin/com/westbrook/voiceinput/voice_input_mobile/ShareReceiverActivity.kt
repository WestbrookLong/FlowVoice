package com.westbrook.voiceinput.voice_input_mobile

import android.app.Activity
import android.content.Intent
import android.database.Cursor
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.widget.Toast

class ShareReceiverActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uris = sharedUris(intent)
        if (uris.isEmpty()) {
            finishWithMessage("No file was shared.")
            return
        }

        Thread {
            var files: List<FlowUploadFile> = emptyList()
            try {
                files = uris.map { uri ->
                    FlowFileUploader.cacheUri(
                        this,
                        uri,
                        queryDisplayName(uri) ?: "shared-${System.currentTimeMillis()}",
                        contentResolver.getType(uri) ?: "application/octet-stream",
                        "shared_uploads",
                    )
                }
                FlowFileUploader.upload(this, files)
                runOnUiThread { finishWithMessage("Sent to computer.") }
            } catch (error: Exception) {
                val detail = error.message?.take(120).orEmpty()
                runOnUiThread {
                    finishWithMessage(if (detail.isEmpty()) "Upload failed." else "Upload failed: $detail")
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

    private fun queryDisplayName(uri: Uri): String? {
        var cursor: Cursor? = null
        return try {
            cursor = contentResolver.query(
                uri,
                arrayOf(OpenableColumns.DISPLAY_NAME),
                null,
                null,
                null,
            )
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

    companion object {
        const val CONNECTION_PREFS = "flowvoice_share_connection"
        const val KEY_BASE_URL = "base_url"
        const val KEY_TOKEN = "token"
    }
}
