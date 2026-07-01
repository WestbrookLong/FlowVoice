from __future__ import annotations

import math
import random
import sys
import unittest
from array import array
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from asr.endpointing import EndpointConfig, EndpointDetector


SAMPLE_RATE = 16000
FRAME_MS = 100
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


def pcm_from_samples(samples: list[int]) -> bytes:
    values = array("h", samples)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def silence_frame() -> bytes:
    return pcm_from_samples([0] * FRAME_SAMPLES)


def tone_frame(amplitude: int = 900, freq: float = 220.0) -> bytes:
    samples = [
        int(amplitude * math.sin(2.0 * math.pi * freq * index / SAMPLE_RATE))
        for index in range(FRAME_SAMPLES)
    ]
    return pcm_from_samples(samples)


def noise_frame(amplitude: int = 80, seed: int = 1) -> bytes:
    rng = random.Random(seed)
    return pcm_from_samples([rng.randint(-amplitude, amplitude) for _ in range(FRAME_SAMPLES)])


def detector(**overrides) -> EndpointDetector:
    values = {
        "sample_rate": SAMPLE_RATE,
        "frame_ms": FRAME_MS,
        "pre_roll_ms": 300,
        "min_speech_ms": 300,
        "start_trigger_ms": 200,
        "end_silence_ms": 600,
        "max_utterance_ms": 12000,
    }
    values.update(overrides)
    config = EndpointConfig(**values)
    return EndpointDetector(config)


class EndpointDetectorTests(unittest.TestCase):
    def test_silence_does_not_start(self) -> None:
        endpoint = detector()
        decisions = [endpoint.process(silence_frame()) for _ in range(10)]

        self.assertFalse(any(decision.started for decision in decisions))
        self.assertFalse(any(decision.endpoint for decision in decisions))
        self.assertEqual(endpoint.state, "IDLE")

    def test_short_noise_does_not_trigger_speech(self) -> None:
        endpoint = detector()
        decisions = [
            endpoint.process(noise_frame(2500, seed=2)),
            endpoint.process(silence_frame()),
            endpoint.process(silence_frame()),
        ]

        self.assertFalse(any(decision.started for decision in decisions))
        self.assertFalse(any(decision.endpoint for decision in decisions))
        self.assertEqual(endpoint.state, "IDLE")

    def test_speech_start_includes_pre_roll(self) -> None:
        endpoint = detector()
        for _ in range(3):
            endpoint.process(silence_frame())

        first = endpoint.process(tone_frame())
        second = endpoint.process(tone_frame())

        self.assertFalse(first.started)
        self.assertTrue(second.started)
        self.assertGreaterEqual(len(second.frames), 2)
        self.assertEqual(endpoint.state, "SPEECH")

    def test_short_pause_does_not_end_utterance(self) -> None:
        endpoint = detector()
        decisions = []
        for _ in range(3):
            decisions.append(endpoint.process(tone_frame()))
        for _ in range(4):
            decisions.append(endpoint.process(silence_frame()))
        decisions.append(endpoint.process(tone_frame()))

        self.assertTrue(any(decision.started for decision in decisions))
        self.assertFalse(any(decision.endpoint for decision in decisions))
        self.assertEqual(endpoint.state, "SPEECH")

    def test_long_silence_ends_utterance(self) -> None:
        endpoint = detector()
        decisions = []
        for _ in range(4):
            decisions.append(endpoint.process(tone_frame()))
        for _ in range(6):
            decisions.append(endpoint.process(silence_frame()))

        self.assertTrue(decisions[-1].endpoint)
        self.assertEqual(decisions[-1].reason, "silence")
        self.assertFalse(decisions[-1].too_short)
        self.assertEqual(endpoint.state, "IDLE")

    def test_max_duration_forces_endpoint(self) -> None:
        endpoint = detector(max_utterance_ms=500, end_silence_ms=2000)
        decisions = [endpoint.process(tone_frame()) for _ in range(6)]

        self.assertTrue(any(decision.endpoint and decision.reason == "max_duration" for decision in decisions))
        self.assertEqual(endpoint.state, "IDLE")

    def test_audio_drop_resets_active_utterance(self) -> None:
        endpoint = detector()
        endpoint.process(tone_frame())
        endpoint.process(tone_frame())

        decision = endpoint.handle_dropped_frames(2)

        self.assertTrue(decision.endpoint)
        self.assertTrue(decision.reset_asr)
        self.assertEqual(decision.reason, "audio_drop")
        self.assertEqual(endpoint.state, "IDLE")


if __name__ == "__main__":
    unittest.main()
