from __future__ import annotations

import math
import sys
from array import array
from collections import deque
from dataclasses import dataclass, field
from typing import Literal


EndpointState = Literal["IDLE", "SPEECH", "ENDING"]


@dataclass
class EndpointConfig:
    sample_rate: int = 16000
    frame_ms: int = 100
    pre_roll_ms: int = 300
    min_speech_ms: int = 300
    start_trigger_ms: int = 200
    end_silence_ms: int = 650
    max_utterance_ms: int = 12000
    snr_start_db: float = 8.0
    snr_continue_db: float = 4.5
    min_peak: float = 160.0
    initial_noise_rms: float = 80.0
    noise_update_alpha: float = 0.05
    attack_alpha: float = 0.55
    release_alpha: float = 0.18
    noisy_zcr_threshold: float = 0.34
    noisy_zcr_snr_db: float = 18.0


@dataclass
class FrameFeatures:
    rms: float = 0.0
    peak: int = 0
    zcr: float = 0.0
    smooth_rms: float = 0.0
    noise_rms: float = 0.0
    snr_db: float = 0.0


@dataclass
class EndpointDecision:
    frames: list[bytes] = field(default_factory=list)
    started: bool = False
    endpoint: bool = False
    reason: str = ""
    too_short: bool = False
    reset_asr: bool = False
    is_voice: bool = False
    state: EndpointState = "IDLE"
    features: FrameFeatures = field(default_factory=FrameFeatures)
    dropped_frames: int = 0


class EndpointDetector:
    def __init__(self, config: EndpointConfig | None = None) -> None:
        self.config = config or EndpointConfig()
        self.pre_roll_frames = max(1, self.config.pre_roll_ms // self.config.frame_ms)
        self.start_trigger_frames = max(1, math.ceil(self.config.start_trigger_ms / self.config.frame_ms))
        self.end_silence_frames = max(1, math.ceil(self.config.end_silence_ms / self.config.frame_ms))
        self.min_speech_frames = max(1, math.ceil(self.config.min_speech_ms / self.config.frame_ms))
        self.max_utterance_frames = max(1, math.ceil(self.config.max_utterance_ms / self.config.frame_ms))
        self.pre_roll: deque[bytes] = deque(maxlen=self.pre_roll_frames)
        self.state: EndpointState = "IDLE"
        self.noise_rms = max(1.0, self.config.initial_noise_rms)
        self.smooth_rms = self.noise_rms
        self.start_voice_frames = 0
        self.speech_voice_frames = 0
        self.utterance_frames = 0
        self.silence_frames = 0
        self.total_dropped_frames = 0
        self.last_features = FrameFeatures(noise_rms=self.noise_rms, smooth_rms=self.smooth_rms)

    def process(self, pcm: bytes) -> EndpointDecision:
        features = self._extract_features(pcm)
        is_voice = self._is_voice(features)
        frames: list[bytes] = []
        started = False
        endpoint = False
        reason = ""
        too_short = False

        if self.state == "IDLE":
            if is_voice:
                self.start_voice_frames += 1
            else:
                self.start_voice_frames = 0
                self._update_noise_floor(features)

            self.pre_roll.append(pcm)
            if self.start_voice_frames >= self.start_trigger_frames:
                started = True
                self.state = "SPEECH"
                frames = list(self.pre_roll)
                self.pre_roll.clear()
                self.utterance_frames = len(frames)
                self.speech_voice_frames = self.start_voice_frames
                self.silence_frames = 0
            return self._decision(frames, started, endpoint, reason, too_short, False, is_voice, features)

        frames = [pcm]
        self.utterance_frames += 1
        if is_voice:
            self.state = "SPEECH"
            self.speech_voice_frames += 1
            self.silence_frames = 0
        else:
            self.state = "ENDING"
            self.silence_frames += 1

        if self.utterance_frames >= self.max_utterance_frames:
            endpoint = True
            reason = "max_duration"
        elif self.silence_frames >= self.end_silence_frames:
            endpoint = True
            reason = "silence"

        if endpoint:
            too_short = self.speech_voice_frames < self.min_speech_frames
            if too_short:
                reason = "too_short"
            self._reset_utterance_state()

        return self._decision(frames, started, endpoint, reason, too_short, False, is_voice, features)

    def handle_dropped_frames(self, count: int) -> EndpointDecision:
        if count <= 0:
            return EndpointDecision(state=self.state, features=self.last_features)
        self.total_dropped_frames += count
        if self.state == "IDLE":
            return EndpointDecision(state=self.state, features=self.last_features, dropped_frames=count)

        self._reset_utterance_state()
        return EndpointDecision(
            endpoint=True,
            reason="audio_drop",
            too_short=True,
            reset_asr=True,
            state=self.state,
            features=self.last_features,
            dropped_frames=count,
        )

    def reset(self) -> None:
        self.pre_roll.clear()
        self._reset_utterance_state()
        self.start_voice_frames = 0

    def _reset_utterance_state(self) -> None:
        self.state = "IDLE"
        self.start_voice_frames = 0
        self.speech_voice_frames = 0
        self.utterance_frames = 0
        self.silence_frames = 0

    def _extract_features(self, pcm: bytes) -> FrameFeatures:
        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            features = FrameFeatures(noise_rms=self.noise_rms, smooth_rms=self.smooth_rms)
            self.last_features = features
            return features

        total = 0.0
        peak = 0
        zero_crossings = 0
        previous_sign = 0
        for sample in samples:
            abs_sample = abs(sample)
            peak = max(peak, abs_sample)
            total += float(sample) * float(sample)
            sign = 1 if sample > 0 else -1 if sample < 0 else previous_sign
            if previous_sign and sign and sign != previous_sign:
                zero_crossings += 1
            if sign:
                previous_sign = sign

        rms = math.sqrt(total / len(samples))
        zcr = zero_crossings / max(1, len(samples) - 1)
        alpha = self.config.attack_alpha if rms > self.smooth_rms else self.config.release_alpha
        self.smooth_rms = alpha * rms + (1.0 - alpha) * self.smooth_rms
        snr_db = 20.0 * math.log10((self.smooth_rms + 1.0) / (self.noise_rms + 1.0))
        features = FrameFeatures(
            rms=rms,
            peak=peak,
            zcr=zcr,
            smooth_rms=self.smooth_rms,
            noise_rms=self.noise_rms,
            snr_db=snr_db,
        )
        self.last_features = features
        return features

    def _is_voice(self, features: FrameFeatures) -> bool:
        threshold = self.config.snr_start_db if self.state == "IDLE" else self.config.snr_continue_db
        has_energy = features.snr_db >= threshold and features.peak >= self.config.min_peak
        looks_like_hiss = features.zcr >= self.config.noisy_zcr_threshold and features.snr_db < self.config.noisy_zcr_snr_db
        return has_energy and not looks_like_hiss

    def _update_noise_floor(self, features: FrameFeatures) -> None:
        target = max(1.0, features.rms)
        alpha = self.config.noise_update_alpha
        self.noise_rms = (1.0 - alpha) * self.noise_rms + alpha * target

    def _decision(
        self,
        frames: list[bytes],
        started: bool,
        endpoint: bool,
        reason: str,
        too_short: bool,
        reset_asr: bool,
        is_voice: bool,
        features: FrameFeatures,
    ) -> EndpointDecision:
        return EndpointDecision(
            frames=frames,
            started=started,
            endpoint=endpoint,
            reason=reason,
            too_short=too_short,
            reset_asr=reset_asr,
            is_voice=is_voice,
            state=self.state,
            features=features,
        )
