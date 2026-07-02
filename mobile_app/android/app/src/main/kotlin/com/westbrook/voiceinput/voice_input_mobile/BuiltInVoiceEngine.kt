package com.westbrook.voiceinput.voice_input_mobile

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.Looper
import com.k2fsa.sherpa.onnx.EndpointConfig
import com.k2fsa.sherpa.onnx.EndpointRule
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineRecognizer
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import java.util.concurrent.atomic.AtomicBoolean

object BuiltInVoiceEngine {
    private const val SAMPLE_RATE = 16000
    private const val MODEL_DIR = "sherpa-onnx-lstm-zh-2023-02-20"

    private val mainHandler = Handler(Looper.getMainLooper())
    private val recording = AtomicBoolean(false)

    private var worker: Thread? = null
    private var recorder: AudioRecord? = null
    private var recognizer: OnlineRecognizer? = null

    fun start(
        context: Context,
        onText: (String, Boolean) -> Unit,
        onStatus: (String) -> Unit,
    ): Boolean {
        if (recording.get()) {
            postStatus(onStatus, "listening")
            return true
        }
        if (!hasAudioPermission(context)) {
            postStatus(onStatus, "permission_missing")
            return false
        }
        recording.set(true)
        worker = Thread({
            runRecognizer(context.applicationContext, onText, onStatus)
        }, "flowvoice-sherpa-recognizer").also { it.start() }
        return true
    }

    fun stop() {
        recording.set(false)
        recorder?.stopSafely()
        worker?.interrupt()
        worker = null
    }

    fun isRecording(): Boolean = recording.get()

    private fun runRecognizer(
        context: Context,
        onText: (String, Boolean) -> Unit,
        onStatus: (String) -> Unit,
    ) {
        var stream: com.k2fsa.sherpa.onnx.OnlineStream? = null
        try {
            postStatus(onStatus, "loading")
            val activeRecognizer = recognizer ?: createRecognizer(context).also { recognizer = it }
            stream = activeRecognizer.createStream()
            val bufferSize = maxOf(
                AudioRecord.getMinBufferSize(
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                ),
                SAMPLE_RATE,
            )
            val audioRecord = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize,
            )
            recorder = audioRecord
            audioRecord.startRecording()
            postStatus(onStatus, "listening")

            val shortBuffer = ShortArray(bufferSize / 2)
            var lastPartial = ""
            while (recording.get()) {
                val read = audioRecord.read(shortBuffer, 0, shortBuffer.size)
                if (read <= 0) {
                    continue
                }
                val samples = FloatArray(read)
                for (i in 0 until read) {
                    samples[i] = shortBuffer[i] / 32768.0f
                }
                val currentStream = stream ?: break
                currentStream.acceptWaveform(samples, SAMPLE_RATE)
                while (activeRecognizer.isReady(currentStream)) {
                    activeRecognizer.decode(currentStream)
                }
                val partial = activeRecognizer.getResult(currentStream).text.trim()
                if (partial.isNotEmpty() && partial != lastPartial) {
                    lastPartial = partial
                    postText(onText, partial, false)
                }
                if (activeRecognizer.isEndpoint(currentStream)) {
                    if (partial.isNotEmpty()) {
                        postText(onText, partial, true)
                    }
                    activeRecognizer.reset(currentStream)
                    lastPartial = ""
                }
            }

            stream?.inputFinished()
            while (stream != null && activeRecognizer.isReady(stream)) {
                activeRecognizer.decode(stream)
            }
            val finalText = stream?.let { activeRecognizer.getResult(it).text.trim() }.orEmpty()
            if (finalText.isNotEmpty()) {
                postText(onText, finalText, true)
            }
            postStatus(onStatus, "stopped")
        } catch (t: Throwable) {
            postStatus(onStatus, "error:${t.message ?: t.javaClass.simpleName}")
        } finally {
            recording.set(false)
            stream?.release()
            recorder?.releaseSafely()
            recorder = null
        }
    }

    private fun createRecognizer(context: Context): OnlineRecognizer {
        val modelConfig = OnlineModelConfig(
            transducer = OnlineTransducerModelConfig(
                encoder = "$MODEL_DIR/encoder-epoch-11-avg-1.int8.onnx",
                decoder = "$MODEL_DIR/decoder-epoch-11-avg-1.int8.onnx",
                joiner = "$MODEL_DIR/joiner-epoch-11-avg-1.int8.onnx",
            ),
            tokens = "$MODEL_DIR/tokens.txt",
            numThreads = 2,
            modelType = "lstm",
        )
        val config = OnlineRecognizerConfig(
            featConfig = FeatureConfig(sampleRate = SAMPLE_RATE),
            modelConfig = modelConfig,
            endpointConfig = EndpointConfig(
                rule1 = EndpointRule(false, 2.4f, 0.0f),
                rule2 = EndpointRule(true, 1.2f, 0.0f),
                rule3 = EndpointRule(false, 0.0f, 18.0f),
            ),
            enableEndpoint = true,
        )
        return OnlineRecognizer(assetManager = context.assets, config = config)
    }

    private fun hasAudioPermission(context: Context): Boolean {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M ||
            context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    }

    private fun postText(callback: (String, Boolean) -> Unit, text: String, isFinal: Boolean) {
        mainHandler.post { callback(text, isFinal) }
    }

    private fun postStatus(callback: (String) -> Unit, status: String) {
        mainHandler.post { callback(status) }
    }

    private fun AudioRecord.stopSafely() {
        try {
            stop()
        } catch (_: IllegalStateException) {
        }
    }

    private fun AudioRecord.releaseSafely() {
        try {
            release()
        } catch (_: RuntimeException) {
        }
    }
}
