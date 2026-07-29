from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

StateCallback = Callable[[dict[str, Any]], None]


class VoiceAskManager:
    def __init__(
        self,
        *,
        model: str = "qwen-plus",
        api_key: str = "",
        on_strip_state: StateCallback | None = None,
        on_result_state: StateCallback | None = None,
    ) -> None:
        self.lock = threading.RLock()
        self.enabled = True
        self.model = model
        self.api_key = api_key.strip()
        self.on_strip_state = on_strip_state
        self.on_result_state = on_result_state
        self.status = "idle"
        self.source = "mobile"
        self.prompt = ""
        self.answer = ""
        self.error: str | None = None
        self.target_hwnd = 0
        self.latest_source_text = {"mobile": "", "desktop": ""}
        self.request_id = 0
        self.result_visible = False

    def configured(self) -> bool:
        with self.lock:
            return bool(self.api_key or os.environ.get("DASHSCOPE_API_KEY", "").strip())

    def set_enabled(self, enabled: bool) -> None:
        with self.lock:
            self.enabled = bool(enabled)
            if self.enabled or self.status != "listening":
                return
        self.cancel_listening()

    def set_model(self, model: str) -> None:
        cleaned = str(model).strip()
        if not cleaned:
            raise ValueError("Voice Ask model cannot be empty.")
        with self.lock:
            self.model = cleaned

    def set_api_key(self, api_key: str) -> None:
        with self.lock:
            self.api_key = str(api_key).strip()

    def observe_source(self, source: str, text: str) -> None:
        if source not in self.latest_source_text:
            return
        with self.lock:
            self.latest_source_text[source] = text

    def start(self, *, source: str, target_hwnd: int = 0) -> dict[str, Any]:
        if source not in self.latest_source_text:
            raise ValueError("Unsupported Voice Ask source.")
        with self.lock:
            if not self.enabled:
                raise RuntimeError("Voice Ask is disabled.")
            self.request_id += 1
            self.status = "listening"
            self.source = source
            self.prompt = ""
            self.answer = ""
            self.error = None
            self.target_hwnd = int(target_hwnd or 0)
            self.result_visible = False
            snapshot = self.snapshot()
        self._notify_strip(snapshot)
        return snapshot

    def should_capture(self, source: str) -> bool:
        with self.lock:
            return self.enabled and self.status == "listening" and self.source == source

    def set_prompt(self, prompt: str) -> dict[str, Any]:
        with self.lock:
            if self.status != "listening":
                return self.snapshot()
            self.prompt = str(prompt)
            return self.snapshot()

    def stop_and_submit(self) -> dict[str, Any]:
        with self.lock:
            if self.status != "listening":
                return self.snapshot()
            self.status = "thinking"
            self.answer = ""
            self.error = None
            self.result_visible = True
            self.request_id += 1
            request_id = self.request_id
            prompt = self.prompt.strip()
            model = self.model
            snapshot = self.snapshot()
        self._notify_strip(snapshot)
        self._notify_result(snapshot)
        threading.Thread(
            target=self._complete_request,
            args=(request_id, prompt, model),
            daemon=True,
            name="voice-ask-request",
        ).start()
        return snapshot

    def cancel_listening(self) -> dict[str, Any]:
        with self.lock:
            if self.status == "listening":
                self.status = "idle"
                self.request_id += 1
            snapshot = self.snapshot()
        self._notify_strip({"status": "hidden", "prompt": ""})
        return snapshot

    def dismiss_result(self) -> dict[str, Any]:
        with self.lock:
            self.result_visible = False
            self.request_id += 1
            if self.status in {"thinking", "completed", "error"}:
                self.status = "idle"
            snapshot = self.snapshot()
        self._notify_strip({"status": "hidden", "prompt": ""})
        self._notify_result(snapshot)
        return snapshot

    def result_for_insertion(self) -> tuple[str, int]:
        with self.lock:
            if self.status != "completed" or not self.answer:
                return "", self.target_hwnd
            answer = self.answer
            target_hwnd = self.target_hwnd
            self.result_visible = False
            self.status = "idle"
            self.request_id += 1
            snapshot = self.snapshot()
        self._notify_strip({"status": "hidden", "prompt": ""})
        self._notify_result(snapshot)
        return answer, target_hwnd

    def result_for_copy(self) -> str:
        with self.lock:
            if self.status != "completed" or not self.answer:
                return ""
            answer = self.answer
            self.result_visible = False
            self.status = "idle"
            self.request_id += 1
            snapshot = self.snapshot()
        self._notify_strip({"status": "hidden", "prompt": ""})
        self._notify_result(snapshot)
        return answer

    def target_window(self) -> int:
        with self.lock:
            return self.target_hwnd

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "enabled": self.enabled,
                "configured": self.configured(),
                "model": self.model,
                "status": self.status,
                "source": self.source,
                "prompt": self.prompt,
                "answer": self.answer,
                "error": self.error,
                "resultVisible": self.result_visible,
            }

    def _complete_request(self, request_id: int, prompt: str, model: str) -> None:
        if not prompt:
            self._finish_request(request_id, error="No Voice Ask prompt was captured.")
            return
        try:
            answer = self._request_qwen(prompt, model)
        except Exception as exc:
            self._finish_request(request_id, error=str(exc))
            return
        self._finish_request(request_id, answer=answer)

    def _finish_request(self, request_id: int, *, answer: str = "", error: str | None = None) -> None:
        with self.lock:
            if request_id != self.request_id or not self.result_visible:
                return
            self.answer = answer
            self.error = error
            self.status = "error" if error else "completed"
            snapshot = self.snapshot()
        self._notify_strip(snapshot)
        self._notify_result(snapshot)

    def _request_qwen(self, prompt: str, model: str) -> str:
        with self.lock:
            configured_key = self.api_key
        api_key = configured_key or os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured.")
        base_url = os.environ.get(
            "FLOWVOICE_VOICE_ASK_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ).strip()
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are FlowVoice Voice Ask. Answer the user's request directly and accurately. "
                        "Use the same language as the user unless they request another language. "
                        "Return readable Markdown and do not mention speech recognition."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }
        request = urllib.request.Request(
            base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
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
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Qwen API returned an invalid answer.")
        return content.strip()

    def _notify_strip(self, snapshot: dict[str, Any]) -> None:
        callback = self.on_strip_state
        if callback is not None:
            callback(snapshot)

    def _notify_result(self, snapshot: dict[str, Any]) -> None:
        callback = self.on_result_state
        if callback is not None:
            callback(snapshot)
