from __future__ import annotations

from typing import Protocol


class TextPolishProvider(Protocol):
    name: str

    def available(self) -> bool:
        ...

    def polish_segment(
        self,
        previous_summaries: list[dict],
        previous_raw_text: str,
        new_raw_text: str,
        style: str,
    ) -> dict:
        ...

    def polish_global(self, raw_text: str, segment_summaries: list[dict], style: str) -> str:
        ...
