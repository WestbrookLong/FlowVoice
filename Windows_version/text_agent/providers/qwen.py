from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from ..prompts import build_global_prompt, build_segment_prompt


class QwenTextPolishProvider:
    name = "qwen"

    def __init__(self) -> None:
        self.api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        self.model = os.environ.get("FLOWVOICE_TEXT_AGENT_MODEL", "qwen-plus").strip() or "qwen-plus"
        self.base_url = os.environ.get(
            "FLOWVOICE_TEXT_AGENT_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ).strip()

    def available(self) -> bool:
        return bool(self.api_key)

    def polish_segment(
        self,
        previous_summaries: list[dict],
        previous_raw_text: str,
        new_raw_text: str,
        style: str,
    ) -> dict:
        if not new_raw_text.strip():
            return {}
        content = self._complete(build_segment_prompt(previous_summaries, previous_raw_text, new_raw_text, style))
        return self._parse_json_object(content)

    def polish_global(self, raw_text: str, segment_summaries: list[dict], style: str) -> str:
        if not raw_text.strip():
            return ""
        return self._complete(build_global_prompt(raw_text, segment_summaries, style))

    def _complete(self, messages: list[dict[str, str]]) -> str:
        if not self.available():
            raise RuntimeError("DASHSCOPE_API_KEY is not configured.")

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Qwen API error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Qwen API request failed: {exc}") from exc

        decoded = json.loads(body)
        choices = decoded.get("choices")
        if not choices:
            raise RuntimeError("Qwen API returned no choices.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise RuntimeError("Qwen API returned an invalid message.")
        return content.strip()

    def _parse_json_object(self, content: str) -> dict:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            decoded = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Qwen segment response is not valid JSON: {content}") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("Qwen segment response must be a JSON object.")
        return decoded
