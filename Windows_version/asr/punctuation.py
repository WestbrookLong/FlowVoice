from __future__ import annotations

from .funasr_offline_engine import extract_text


class PunctuationEngine:
    def __init__(self, punctuation_strategy: str = "spoken") -> None:
        self.punctuation_strategy = punctuation_strategy
        self.model = None
        self.error: str | None = None

    def start(self) -> None:
        if self.punctuation_strategy != "model":
            return
        try:
            from funasr import AutoModel

            try:
                self.model = AutoModel(model="ct-punc", disable_update=True)
            except TypeError:
                self.model = AutoModel(model="ct-punc")
        except Exception as exc:
            self.error = str(exc)
            self.model = None

    def apply_final(self, text: str) -> str:
        if self.punctuation_strategy != "model" or not text:
            return text
        if self.model is None:
            return text
        try:
            return extract_text(self.model.generate(input=text)) or text
        except Exception as exc:
            self.error = str(exc)
            return text

    def apply_partial(self, text: str) -> str:
        return text

    def close(self) -> None:
        self.model = None

