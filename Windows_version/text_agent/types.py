from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TextAgentSession:
    id: str
    style: str
    status: str = "idle"
    raw_text: str = ""
    draft_text: str = ""
    final_text: str = ""
    segment_summaries: list[dict] = field(default_factory=list)
    error: str | None = None
    copied: bool = False
    inserted: bool = False
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    ended_at: str | None = None
    last_polished_raw_len: int = 0
    last_segment_raw_len: int = 0
    polish_count: int = 0
    capture_baseline_text: str = ""
    capture_prefix_text: str = ""
    last_captured_source_text: str = ""
    captured_source_text: str = ""
    revision: int = 0

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "style": self.style,
            "status": self.status,
            "rawText": self.raw_text,
            "draftText": self.draft_text,
            "finalText": self.final_text,
            "segmentSummaries": list(self.segment_summaries),
            "error": self.error,
            "copied": self.copied,
            "inserted": self.inserted,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "endedAt": self.ended_at,
            "rawCharCount": len(self.raw_text),
            "lastPolishedRawLen": self.last_polished_raw_len,
            "lastSegmentRawLen": self.last_segment_raw_len,
            "polishCount": self.polish_count,
            "revision": self.revision,
        }
