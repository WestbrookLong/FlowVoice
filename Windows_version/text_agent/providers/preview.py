from __future__ import annotations

import re

from ..prompts import STYLE_LABELS, normalize_style


FILLER_PATTERN = re.compile(r"(嗯+|啊+|呃+|那个|就是|然后然后|这个这个|这个|就是说)")
SPACE_PATTERN = re.compile(r"\s+")


def _clean(text: str) -> str:
    return SPACE_PATTERN.sub(" ", FILLER_PATTERN.sub("", text)).strip()


class PreviewTextPolishProvider:
    name = "preview"

    def available(self) -> bool:
        return True

    def polish_segment(
        self,
        previous_summaries: list[dict],
        previous_raw_text: str,
        new_raw_text: str,
        style: str,
    ) -> dict:
        cleaned = _clean(new_raw_text)
        if not cleaned:
            return {}
        normalized = normalize_style(style)
        points = [item.strip() for item in re.split(r"[。；;\n]", cleaned) if item.strip()]
        actions = []
        if normalized == "todo_items":
            actions = [{"text": item, "owner": None, "deadline": None} for item in points]
        return {
            "title": points[0][:28] if points else STYLE_LABELS[normalized],
            "summary": cleaned,
            "keyPoints": points or [cleaned],
            "actionItems": actions,
        }

    def polish_global(self, raw_text: str, segment_summaries: list[dict], style: str) -> str:
        cleaned = _clean(raw_text)
        if not cleaned:
            return ""
        normalized = normalize_style(style)
        if segment_summaries and normalized == "meeting_notes":
            joined = "\n".join(
                f"- {item.get('title', '未命名主题')}：{item.get('summary', '')}"
                for item in segment_summaries
            )
            return f"会议纪要\n\n{joined}"
        if normalized == "summary_bullets":
            return "\n".join(f"- {item.strip()}" for item in re.split(r"[。；;\n]", cleaned) if item.strip())
        if normalized == "todo_items":
            return "\n".join(f"- 待办：{item.strip()}" for item in re.split(r"[。；;\n]", cleaned) if item.strip())
        if normalized == "faithful_cleanup":
            return cleaned
        return cleaned
