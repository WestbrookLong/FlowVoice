from __future__ import annotations

import threading


class InputGate:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.paused = False
        self.version = 0

    def is_paused(self) -> bool:
        with self.lock:
            return self.paused

    def set_paused(self, paused: bool) -> bool:
        with self.lock:
            next_paused = bool(paused)
            if self.paused != next_paused:
                self.version += 1
            self.paused = next_paused
            return self.paused

    def toggle(self) -> bool:
        with self.lock:
            self.paused = not self.paused
            self.version += 1
            return self.paused

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "paused": self.paused,
                "label": "Alt+M",
                "version": self.version,
            }
