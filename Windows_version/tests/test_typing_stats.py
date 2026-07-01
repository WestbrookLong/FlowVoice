from datetime import date, datetime

from typing_stats import TypingStats, count_visible_characters


def test_count_visible_characters_ignores_whitespace():
    assert count_visible_characters("你好 world\n。") == 8


def test_typing_stats_groups_today_week_month_and_sources(tmp_path):
    stats = TypingStats(tmp_path / "typing_stats.json", flush_delay=0)
    stats.record("今天手机", "mobile", at=datetime(2026, 6, 11, 9, 0))
    stats.record("电脑", "computer", at=datetime(2026, 6, 11, 10, 0))
    stats.record("周一", "mobile", at=datetime(2026, 6, 8, 10, 0))
    stats.record("上月", "computer", at=datetime(2026, 5, 31, 10, 0))

    snapshot = stats.snapshot(today=date(2026, 6, 11))

    assert snapshot["today"] == {"total": 6, "mobile": 4, "computer": 2}
    assert snapshot["week"] == {"total": 8, "mobile": 6, "computer": 2}
    assert snapshot["month"] == {"total": 8, "mobile": 6, "computer": 2}
    assert snapshot["history"][-1] == {
        "date": "2026-06-11",
        "total": 6,
        "mobile": 4,
        "computer": 2,
    }


def test_typing_stats_persists_data(tmp_path):
    path = tmp_path / "typing_stats.json"
    stats = TypingStats(path, flush_delay=0)
    stats.record("持久化", "mobile", at=datetime(2026, 6, 11, 9, 0))
    stats.close()

    restored = TypingStats(path, flush_delay=0)
    assert restored.snapshot(today=date(2026, 6, 11))["today"]["total"] == 3
