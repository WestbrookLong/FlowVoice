from __future__ import annotations

import json
import secrets
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

from .prompts import normalize_style
from .providers import PreviewTextPolishProvider, QwenTextPolishProvider
from .providers.base import TextPolishProvider
from .types import TextAgentSession, utc_now_iso


CopyCallback = Callable[[str], None]
InsertCallback = Callable[[str], None]


class TextAgentManager:
    def __init__(
        self,
        *,
        copy_callback: CopyCallback | None = None,
        insert_callback: InsertCallback | None = None,
        provider: TextPolishProvider | None = None,
        history_path: Path | None = None,
        trigger_chars: int = 80,
        active_tail_chars: int = 15,
        max_sessions: int = 20,
    ) -> None:
        self.lock = threading.RLock()
        self.copy_callback = copy_callback
        self.insert_callback = insert_callback
        self.provider = provider or self._default_provider()
        self.history_path = history_path
        self.trigger_chars = max(20, trigger_chars)
        self.active_tail_chars = max(0, active_tail_chars)
        self.max_sessions = max_sessions
        self.mode_enabled = False
        self.recording = False
        self.paused = False
        self.last_mobile_text = ""
        self.active_session_id: str | None = None
        self.sessions: dict[str, TextAgentSession] = {}
        self.session_order: deque[str] = deque()
        self.polish_thread: threading.Thread | None = None

    def _default_provider(self) -> TextPolishProvider:
        qwen = QwenTextPolishProvider()
        if qwen.available():
            return qwen
        return PreviewTextPolishProvider()

    def provider_name(self) -> str:
        return getattr(self.provider, "name", "unknown")

    def set_mode(self, enabled: bool) -> None:
        with self.lock:
            self.mode_enabled = bool(enabled)
            if not self.mode_enabled:
                self.recording = False
                self.paused = False

    def should_capture_text(self) -> bool:
        with self.lock:
            return self.mode_enabled and self.recording and not self.paused

    def observe_mobile_text(self, text: str) -> None:
        with self.lock:
            self.last_mobile_text = text

    def get_last_mobile_text(self) -> str:
        with self.lock:
            return self.last_mobile_text

    def reset_capture_baseline(self, source_text: str) -> None:
        with self.lock:
            self.last_mobile_text = source_text
            if not self.active_session_id or self.active_session_id not in self.sessions:
                return
            session = self.sessions[self.active_session_id]
            session.capture_baseline_text = source_text
            session.capture_prefix_text = session.raw_text
            session.last_captured_source_text = source_text
            session.captured_source_text = ""
            session.touch()

    def start(self, style: str = "meeting_notes") -> TextAgentSession:
        with self.lock:
            session_id = secrets.token_urlsafe(8)
            session = TextAgentSession(id=session_id, style=normalize_style(style), status="recording")
            self.sessions[session_id] = session
            self.session_order.append(session_id)
            self.active_session_id = session_id
            self.mode_enabled = True
            self.recording = True
            self.paused = False
            session.capture_baseline_text = self.last_mobile_text
            session.capture_prefix_text = ""
            session.last_captured_source_text = self.last_mobile_text
            session.captured_source_text = ""
            self._trim_sessions()
            return session

    def pause(self) -> TextAgentSession:
        with self.lock:
            session = self._get_or_active()
            self.recording = False
            self.paused = True
            session.status = "paused"
            session.touch()
            return session

    def resume(self) -> TextAgentSession:
        with self.lock:
            session = self._get_or_active()
            self.mode_enabled = True
            self.recording = True
            self.paused = False
            session.status = "recording"
            session.capture_baseline_text = self.last_mobile_text
            session.capture_prefix_text = session.raw_text
            session.last_captured_source_text = self.last_mobile_text
            session.captured_source_text = ""
            session.touch()
            return session

    def stop(self, *, copy: bool = True, insert: bool = False) -> TextAgentSession:
        with self.lock:
            session = self._get_or_active()
            thread = self.polish_thread
            self.recording = False
            self.paused = False
            session.status = "finalizing"
            session.touch()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=60)
        with self.lock:
            session = self.sessions[session.id]
            has_unpolished_tail = len(session.raw_text) > session.last_segment_raw_len
        if has_unpolished_tail:
            self._polish_session(session.id, incremental=True, include_active_tail=True)
        session = self._polish_session(session.id, incremental=False)
        text = session.final_text or session.draft_text or session.raw_text
        if copy and self.copy_callback is not None and text:
            self.copy_callback(text)
            with self.lock:
                session = self.sessions[session.id]
                session.copied = True
                session.touch()
        if insert and self.insert_callback is not None and text:
            self.insert_callback(text)
            with self.lock:
                session = self.sessions[session.id]
                session.inserted = True
                session.touch()
        with self.lock:
            session = self.sessions[session.id]
            session.status = "done"
            session.ended_at = utc_now_iso()
            session.touch()
        self._save_history(session)
        return self._clear_temp_after_stop(session.id)

    def toggle_recording(self, style: str = "meeting_notes") -> TextAgentSession:
        with self.lock:
            should_stop = (self.recording or self.paused) and self.active_session_id in self.sessions
        if should_stop:
            return self.stop(copy=True, insert=False)
        return self.start(style)

    def capture_active_source(self, source_text: str) -> str:
        with self.lock:
            session = self._get_or_active()
            baseline = session.capture_baseline_text
            if source_text.startswith(baseline):
                return source_text[len(baseline) :]

            previous_source = session.last_captured_source_text
            previous_active = session.captured_source_text
            prefix_len = self._common_prefix_len(previous_source, source_text)
            active_start = max(0, len(previous_source) - len(previous_active))
            preserved_active_len = max(0, min(len(previous_active), prefix_len - active_start))
            return f"{previous_active[:preserved_active_len]}{source_text[prefix_len:]}"

    def update_text(
        self,
        source_text: str,
        processed_text: str | None = None,
        *,
        active_source_text: str | None = None,
    ) -> TextAgentSession | None:
        with self.lock:
            self.last_mobile_text = source_text
            if not self.mode_enabled or not self.recording or self.paused:
                return None
            session = self._get_or_active()
            if active_source_text is None:
                active_source_text = self.capture_active_source(source_text)
            captured_text = active_source_text if processed_text is None else processed_text
            next_text = f"{session.capture_prefix_text}{captured_text}"
            self._replace_session_text_locked(session, next_text)
            session.captured_source_text = active_source_text
            session.last_captured_source_text = source_text
            session.status = "recording"
            session.error = None
            session.touch()
            stable_length = max(0, len(session.raw_text) - self.active_tail_chars)
            should_polish = stable_length - session.last_segment_raw_len >= self.trigger_chars
            if should_polish:
                self._schedule_polish_locked(session.id)
            return session

    def rerun(self, style: str | None = None) -> TextAgentSession:
        with self.lock:
            session = self._get_or_active()
            if style:
                session.style = normalize_style(style)
            session.status = "polishing"
            session.touch()
        return self._polish_session(session.id, incremental=False)

    def copy_result(self) -> TextAgentSession:
        with self.lock:
            session = self._get_or_active()
            text = session.final_text or session.draft_text or session.raw_text
        if self.copy_callback is None:
            raise RuntimeError("Copy callback is not configured.")
        self.copy_callback(text)
        with self.lock:
            session = self.sessions[session.id]
            session.copied = True
            session.touch()
            return session

    def copy_partial_notes(self) -> str:
        with self.lock:
            session = self._get_or_active()
            markdown = self.segments_to_markdown(session.segment_summaries)
        if not markdown:
            raise RuntimeError("No partial meeting notes are available yet.")
        if self.copy_callback is None:
            raise RuntimeError("Copy callback is not configured.")
        self.copy_callback(markdown)
        return markdown

    @staticmethod
    def segments_to_markdown(segments: list[dict]) -> str:
        if not segments:
            return ""
        lines = ["# 实时会议纪要"]
        for index, segment in enumerate(segments, start=1):
            title = str(segment.get("title", "")).strip() or f"会议片段 {index}"
            summary = str(segment.get("summary", "")).strip()
            key_points = segment.get("keyPoints")
            action_items = segment.get("actionItems")

            lines.extend(["", f"## {index}. {title}"])
            if summary:
                lines.extend(["", summary])
            if isinstance(key_points, list) and key_points:
                points = [str(item).strip() for item in key_points if str(item).strip()]
                if points:
                    lines.extend(["", "### 要点"])
                    lines.extend(f"- {point}" for point in points)
            if isinstance(action_items, list) and action_items:
                actions = []
                for item in action_items:
                    if isinstance(item, dict):
                        text = str(item.get("text", "")).strip()
                        if not text:
                            continue
                        metadata = []
                        if item.get("owner"):
                            metadata.append(f"负责人：{item['owner']}")
                        if item.get("deadline"):
                            metadata.append(f"截止时间：{item['deadline']}")
                        suffix = f"（{'；'.join(metadata)}）" if metadata else ""
                        actions.append(f"- [ ] {text}{suffix}")
                    elif str(item).strip():
                        actions.append(f"- [ ] {str(item).strip()}")
                if actions:
                    lines.extend(["", "### 待办事项"])
                    lines.extend(actions)
        return "\n".join(lines).strip()

    def insert_result(self) -> TextAgentSession:
        with self.lock:
            session = self._get_or_active()
            text = session.final_text or session.draft_text or session.raw_text
        if self.copy_callback is not None:
            self.copy_callback(text)
        if self.insert_callback is None:
            raise RuntimeError("Insert callback is not configured.")
        self.insert_callback(text)
        with self.lock:
            session = self.sessions[session.id]
            session.copied = True
            session.inserted = True
            session.touch()
            return session

    def get_state(self) -> dict:
        with self.lock:
            active = self.sessions.get(self.active_session_id or "")
            recent = [
                self._recent_session_snapshot(self.sessions[item])
                for item in list(self.session_order)[-5:]
                if item in self.sessions
            ]
            return {
                "modeEnabled": self.mode_enabled,
                "recording": self.recording,
                "paused": self.paused,
                "provider": self.provider_name(),
                "configured": self.provider_name() != "preview",
                "triggerChars": self.trigger_chars,
                "activeTailChars": self.active_tail_chars,
                "activeSessionId": self.active_session_id,
                "activeSession": active.snapshot() if active else None,
                "recentSessions": recent,
                "polishing": (
                    self.polish_thread is not None
                    and self.polish_thread.is_alive()
                ) or bool(active and active.status in {"polishing", "finalizing"}),
            }

    def get_float_state(self) -> dict:
        with self.lock:
            active = self.sessions.get(self.active_session_id or "")
            status = active.status if active is not None else "idle"
            raw_text = active.raw_text if active is not None else ""
            return {
                "modeEnabled": self.mode_enabled,
                "recording": self.recording,
                "paused": self.paused,
                "polishing": (
                    self.polish_thread is not None
                    and self.polish_thread.is_alive()
                ) or status in {"polishing", "finalizing"},
                "status": status,
                "rawText": raw_text[-600:],
                "completed": status == "done" and bool(active and active.final_text),
            }

    def _recent_session_snapshot(self, session: TextAgentSession) -> dict:
        return {
            "id": session.id,
            "style": session.style,
            "status": session.status,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
            "endedAt": session.ended_at,
            "copied": session.copied,
            "segmentCount": len(session.segment_summaries),
            "finalPreview": session.final_text[:160],
        }

    def _schedule_polish_locked(self, session_id: str) -> None:
        if self.polish_thread is not None and self.polish_thread.is_alive():
            return
        thread = threading.Thread(target=self._polish_worker, args=(session_id,), daemon=True)
        self.polish_thread = thread
        thread.start()

    def _polish_worker(self, session_id: str) -> None:
        self._polish_session(session_id, incremental=True)
        with self.lock:
            self.polish_thread = None
            session = self.sessions.get(session_id)
            if (
                session is not None
                and self.recording
                and not self.paused
                and max(0, len(session.raw_text) - self.active_tail_chars) - session.last_segment_raw_len
                >= self.trigger_chars
            ):
                self._schedule_polish_locked(session_id)

    def _polish_session(
        self,
        session_id: str,
        *,
        incremental: bool,
        include_active_tail: bool = False,
    ) -> TextAgentSession:
        with self.lock:
            session = self.sessions[session_id]
            raw_text = session.raw_text
            style = session.style
            previous_summaries = list(session.segment_summaries)
            previous_raw_text = raw_text[: session.last_segment_raw_len]
            segment_end = len(raw_text)
            if incremental and not include_active_tail:
                segment_end = max(session.last_segment_raw_len, len(raw_text) - self.active_tail_chars)
            new_raw_text = raw_text[session.last_segment_raw_len : segment_end]
            segment_start = session.last_segment_raw_len
            revision = session.revision
            if not raw_text.strip():
                session.status = "recording" if incremental else "done"
                session.touch()
                return session
            if incremental:
                if not new_raw_text.strip():
                    session.status = "recording" if self.recording else "idle"
                    session.touch()
                    return session
                session.status = "polishing"
            session.touch()

        try:
            if incremental:
                result = self.provider.polish_segment(previous_summaries, previous_raw_text, new_raw_text, style)
            else:
                result = self.provider.polish_global(raw_text, previous_summaries, style)
        except Exception as exc:
            with self.lock:
                session = self.sessions[session_id]
                session.status = "failed"
                session.error = str(exc)
                session.touch()
                return session

        with self.lock:
            session = self.sessions[session_id]
            if incremental and session.revision != revision:
                session.status = "recording" if self.recording else "paused" if self.paused else "idle"
                session.touch()
                return session
            if incremental:
                if result:
                    segment = self._normalize_segment(
                        result,
                        index=len(session.segment_summaries),
                        start_char=segment_start,
                        end_char=segment_start + len(new_raw_text),
                    )
                    session.segment_summaries.append(segment)
                session.draft_text = "\n\n".join(
                    item.get("summary", "") for item in session.segment_summaries if item.get("summary")
                )
                session.status = "recording" if self.recording else "paused" if self.paused else "idle"
                session.last_segment_raw_len = max(session.last_segment_raw_len, segment_end)
            else:
                session.final_text = result
                session.draft_text = result
                session.status = "done"
            session.last_polished_raw_len = len(session.raw_text)
            session.polish_count += 1
            session.error = None
            session.touch()
            return session

    def _normalize_segment(self, value: dict, *, index: int, start_char: int, end_char: int) -> dict:
        source = value if isinstance(value, dict) else {}
        title = str(source.get("title", "")).strip() or f"会议片段 {index + 1}"
        summary = str(source.get("summary", "")).strip()
        key_points = source.get("keyPoints")
        if not isinstance(key_points, list):
            key_points = []
        normalized_points = [str(item).strip() for item in key_points if str(item).strip()]
        action_items = source.get("actionItems")
        if not isinstance(action_items, list):
            action_items = []
        normalized_actions = []
        for item in action_items:
            if isinstance(item, dict):
                text = str(item.get("text", "")).strip()
                if not text:
                    continue
                normalized_actions.append(
                    {
                        "text": text,
                        "owner": item.get("owner"),
                        "deadline": item.get("deadline"),
                    }
                )
            elif str(item).strip():
                normalized_actions.append({"text": str(item).strip(), "owner": None, "deadline": None})
        return {
            "id": f"segment-{index + 1}",
            "index": index + 1,
            "startChar": start_char,
            "endChar": end_char,
            "createdAt": utc_now_iso(),
            "title": title,
            "summary": summary,
            "keyPoints": normalized_points,
            "actionItems": normalized_actions,
        }

    def _replace_session_text_locked(self, session: TextAgentSession, next_text: str) -> None:
        if next_text == session.raw_text:
            return
        prefix_len = self._common_prefix_len(session.raw_text, next_text)
        if prefix_len < session.last_segment_raw_len:
            first_affected = next(
                (
                    index
                    for index, segment in enumerate(session.segment_summaries)
                    if int(segment.get("endChar", 0)) > prefix_len
                ),
                len(session.segment_summaries),
            )
            if first_affected < len(session.segment_summaries):
                rollback_at = int(session.segment_summaries[first_affected].get("startChar", 0))
                session.segment_summaries = session.segment_summaries[:first_affected]
                session.last_segment_raw_len = rollback_at
                session.last_polished_raw_len = min(session.last_polished_raw_len, rollback_at)
                session.draft_text = "\n\n".join(
                    item.get("summary", "") for item in session.segment_summaries if item.get("summary")
                )
        session.raw_text = next_text
        session.revision += 1

    @staticmethod
    def _common_prefix_len(left: str, right: str) -> int:
        length = min(len(left), len(right))
        index = 0
        while index < length and left[index] == right[index]:
            index += 1
        return index

    def _clear_temp_after_stop(self, session_id: str) -> TextAgentSession:
        with self.lock:
            session = self.sessions[session_id]
            session.raw_text = ""
            session.last_polished_raw_len = 0
            session.last_segment_raw_len = 0
            session.capture_baseline_text = self.last_mobile_text
            session.capture_prefix_text = ""
            session.last_captured_source_text = self.last_mobile_text
            session.captured_source_text = ""
            session.touch()
            return session

    def _get_or_active(self) -> TextAgentSession:
        if self.active_session_id and self.active_session_id in self.sessions:
            return self.sessions[self.active_session_id]
        return self.start()

    def _trim_sessions(self) -> None:
        while len(self.session_order) > self.max_sessions:
            old_id = self.session_order.popleft()
            self.sessions.pop(old_id, None)

    def _save_history(self, session: TextAgentSession) -> None:
        if self.history_path is None:
            return
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(session.snapshot(), ensure_ascii=False) + "\n")
