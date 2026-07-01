from __future__ import annotations

import base64
import json
import os
import platform
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .base import ASREvent, StreamingASREngine
from .funasr_offline_engine import normalize_text


SAMPLE_RATE = 16000
DEFAULT_BAIDU_DEV_PID = "80001"
BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_ASR_URL = "https://vop.baidu.com/pro_api"


@dataclass
class BaiduToken:
    value: str
    expires_at: float


class BaiduSpeechEngine(StreamingASREngine):
    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        dev_pid: str = DEFAULT_BAIDU_DEV_PID,
        cuid: str | None = None,
        timeout: float = 12.0,
    ) -> None:
        self.api_key = (api_key or os.environ.get("FLOWVOICE_BAIDU_API_KEY", "")).strip()
        self.secret_key = (secret_key or os.environ.get("FLOWVOICE_BAIDU_SECRET_KEY", "")).strip()
        self.dev_pid = str(os.environ.get("FLOWVOICE_BAIDU_DEV_PID", dev_pid or DEFAULT_BAIDU_DEV_PID)).strip()
        self.cuid = (cuid or os.environ.get("FLOWVOICE_BAIDU_CUID", "")).strip() or self._default_cuid()
        self.timeout = timeout
        self.chunks: list[bytes] = []
        self.token: BaiduToken | None = None

    def start(self) -> None:
        if not self.api_key or not self.secret_key:
            raise RuntimeError(
                "Baidu ASR is not configured. Set FLOWVOICE_BAIDU_API_KEY and FLOWVOICE_BAIDU_SECRET_KEY."
            )
        self._get_token()

    def accept_audio(self, pcm: bytes) -> list[ASREvent]:
        if pcm:
            self.chunks.append(pcm)
        return []

    def finalize(self) -> list[ASREvent]:
        if not self.chunks:
            return []
        audio = b"".join(self.chunks)
        self.chunks = []
        if not audio:
            return []
        try:
            text = self._recognize(audio)
        except Exception as exc:
            return [ASREvent(type="error", text="", error=f"Baidu ASR failed: {exc}")]
        return [ASREvent(type="final", text=text)] if text else []

    def reset(self) -> None:
        self.chunks = []

    def close(self) -> None:
        self.reset()

    def _recognize(self, pcm: bytes) -> str:
        token = self._get_token()
        payload = {
            "format": "pcm",
            "rate": SAMPLE_RATE,
            "channel": 1,
            "cuid": self.cuid,
            "token": token,
            "dev_pid": int(self.dev_pid) if self.dev_pid.isdigit() else self.dev_pid,
            "speech": base64.b64encode(pcm).decode("ascii"),
            "len": len(pcm),
        }
        response = self._post_json(BAIDU_ASR_URL, payload)
        err_no = int(response.get("err_no", -1))
        if err_no != 0:
            err_msg = response.get("err_msg") or response.get("error_msg") or response
            raise RuntimeError(f"err_no={err_no}, err_msg={err_msg}")
        result = response.get("result") or []
        if isinstance(result, list):
            return normalize_text("".join(str(part) for part in result))
        return normalize_text(str(result))

    def _get_token(self) -> str:
        now = time.time()
        if self.token is not None and self.token.expires_at - 60 > now:
            return self.token.value

        query = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self.secret_key,
            }
        )
        response = self._get_json(f"{BAIDU_TOKEN_URL}?{query}")
        access_token = str(response.get("access_token", "")).strip()
        if not access_token:
            error = response.get("error_description") or response.get("error") or response
            raise RuntimeError(f"failed to get Baidu access token: {error}")
        expires_in = int(response.get("expires_in", 2592000))
        self.token = BaiduToken(access_token, now + max(60, expires_in))
        return access_token

    def _get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, method="GET")
        return self._read_json(request)

    def _post_json(self, url: str, payload: dict) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._read_json(request)

    def _read_json(self, request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected JSON response: {data!r}")
        return data

    def _default_cuid(self) -> str:
        return f"flowvoice-{platform.node() or 'desktop'}"
