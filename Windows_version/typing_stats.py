from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta
from pathlib import Path


VALID_SOURCES = {"mobile", "computer"}


def count_visible_characters(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


class TypingStats:
    def __init__(self, path: Path, flush_delay: float = 1.0) -> None:
        self.path = path
        self.flush_delay = max(0.0, flush_delay)
        self.lock = threading.RLock()
        self.days: dict[str, dict[str, int]] = {}
        self.dirty = False
        self.flush_timer: threading.Timer | None = None
        self._load()

    def record(self, text: str, source: str, *, at: datetime | None = None) -> int:
        count = count_visible_characters(text)
        if count <= 0:
            return 0
        normalized_source = source if source in VALID_SOURCES else "mobile"
        day_key = (at or datetime.now()).date().isoformat()
        with self.lock:
            totals = self.days.setdefault(day_key, {"mobile": 0, "computer": 0})
            totals[normalized_source] = max(0, int(totals.get(normalized_source, 0))) + count
            self.dirty = True
            self._schedule_flush_locked()
        return count

    def snapshot(self, *, today: date | None = None, history_days: int = 90) -> dict:
        current_day = today or date.today()
        week_start = current_day - timedelta(days=current_day.weekday())
        month_start = current_day.replace(day=1)
        history_start = current_day - timedelta(days=max(1, history_days) - 1)

        with self.lock:
            days = {
                key: {
                    "mobile": max(0, int(value.get("mobile", 0))),
                    "computer": max(0, int(value.get("computer", 0))),
                }
                for key, value in self.days.items()
                if isinstance(value, dict)
            }

        def totals_between(start: date, end: date) -> dict:
            mobile = 0
            computer = 0
            cursor = start
            while cursor <= end:
                values = days.get(cursor.isoformat(), {})
                mobile += int(values.get("mobile", 0))
                computer += int(values.get("computer", 0))
                cursor += timedelta(days=1)
            return {"total": mobile + computer, "mobile": mobile, "computer": computer}

        all_time_mobile = sum(int(value.get("mobile", 0)) for value in days.values())
        all_time_computer = sum(int(value.get("computer", 0)) for value in days.values())

        history = []
        cursor = history_start
        while cursor <= current_day:
            values = days.get(cursor.isoformat(), {})
            mobile = int(values.get("mobile", 0))
            computer = int(values.get("computer", 0))
            history.append(
                {
                    "date": cursor.isoformat(),
                    "total": mobile + computer,
                    "mobile": mobile,
                    "computer": computer,
                }
            )
            cursor += timedelta(days=1)

        return {
            "allTime": {
                "total": all_time_mobile + all_time_computer,
                "mobile": all_time_mobile,
                "computer": all_time_computer,
            },
            "today": totals_between(current_day, current_day),
            "week": totals_between(week_start, current_day),
            "month": totals_between(month_start, current_day),
            "history": history,
            "weekStartsOn": week_start.isoformat(),
            "monthStartsOn": month_start.isoformat(),
        }

    def flush(self) -> None:
        with self.lock:
            timer = self.flush_timer
            self.flush_timer = None
            if timer is not None and timer is not threading.current_thread():
                timer.cancel()
            if not self.dirty:
                return
            payload = {"version": 1, "days": self.days}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
            self.dirty = False

    def close(self) -> None:
        self.flush()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            stored_days = payload.get("days", {}) if isinstance(payload, dict) else {}
            if not isinstance(stored_days, dict):
                return
            for key, value in stored_days.items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    continue
                try:
                    date.fromisoformat(key)
                except ValueError:
                    continue
                self.days[key] = {
                    "mobile": max(0, int(value.get("mobile", 0))),
                    "computer": max(0, int(value.get("computer", 0))),
                }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.days = {}

    def _schedule_flush_locked(self) -> None:
        if self.flush_delay == 0:
            self.flush()
            return
        if self.flush_timer is not None:
            return
        timer = threading.Timer(self.flush_delay, self.flush)
        timer.daemon = True
        self.flush_timer = timer
        timer.start()
