from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


ASREventType = Literal["partial", "final", "error"]


@dataclass
class ASREvent:
    type: ASREventType
    text: str
    stable_text: str = ""
    unstable_text: str = ""
    error: str = ""
    source: str = ""
    utterance_id: int = 0
    candidate_spans: list[dict] = field(default_factory=list)


class StreamingASREngine(ABC):
    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def accept_audio(self, pcm: bytes) -> list[ASREvent]:
        raise NotImplementedError

    @abstractmethod
    def finalize(self) -> list[ASREvent]:
        raise NotImplementedError

    def poll_events(self) -> list[ASREvent]:
        return []

    @abstractmethod
    def reset(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
