from __future__ import annotations

from dataclasses import replace

from .candidates import CandidateConfig, CandidateSpan, SemanticCandidateReranker


DEFAULT_BERT_RERANKER_MODEL = "hfl/chinese-macbert-base"


class BertMLMCandidateReranker:
    """Candidate-constrained semantic reranker using a masked-language model.

    The reranker never generates free-form text. It only scores candidates
    already produced by the ASR decoder top-k path and optionally replaces a
    low-confidence span when the MLM prefers another candidate safely enough.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_BERT_RERANKER_MODEL,
        hotwords: str = "",
        config: CandidateConfig | None = None,
        device: str | None = None,
        max_context_chars: int = 96,
        max_scored_tokens: int = 12,
    ) -> None:
        self.model_name = model_name or DEFAULT_BERT_RERANKER_MODEL
        self.hotwords = [line.strip() for line in hotwords.splitlines() if line.strip()]
        self.config = config or CandidateConfig()
        self.device = device
        self.max_context_chars = max_context_chars
        self.max_scored_tokens = max_scored_tokens
        self.tokenizer = None
        self.model = None
        self.torch = None
        self.available = False
        self.unavailable_reason = ""
        self.fallback = SemanticCandidateReranker(hotwords=hotwords, config=self.config)

    def start(self) -> None:
        try:
            import torch
            from transformers import AutoModelForMaskedLM, AutoTokenizer

            self.torch = torch
            if self.device is None:
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, use_fast=True)
            self.model = AutoModelForMaskedLM.from_pretrained(self.model_name)
            self.model.to(self.device)
            self.model.eval()
            self.available = True
            self.unavailable_reason = ""
        except Exception as exc:
            self.available = False
            self.unavailable_reason = str(exc)

    def rerank(self, full_text: str, spans: list[CandidateSpan]) -> list[CandidateSpan]:
        if not self.available or self.model is None or self.tokenizer is None or self.torch is None:
            return self.fallback.rerank(full_text, spans)

        reranked: list[CandidateSpan] = []
        for span in spans:
            reranked_span = self._rerank_span(full_text, span)
            if reranked_span is not None:
                reranked.append(reranked_span)
        return reranked

    def _rerank_span(self, full_text: str, span: CandidateSpan) -> CandidateSpan | None:
        position = full_text.find(span.primary_text)
        if position < 0:
            return None

        primary_score = self._score_candidate(full_text, span, span.primary_text)
        if primary_score is None:
            return None

        best_text = span.primary_text
        best_score = primary_score
        best_gain = 0.0
        for rank, candidate in enumerate(span.candidates):
            if not candidate:
                continue
            semantic_score = self._score_candidate(full_text, span, candidate)
            if semantic_score is None:
                continue
            hotword_bonus = self._hotword_bonus(full_text, span, candidate)
            acoustic_prior = max(0.0, 0.05 - 0.01 * rank)
            score = semantic_score + hotword_bonus + acoustic_prior
            gain = score - primary_score
            if score > best_score:
                best_score = score
                best_gain = gain
                best_text = candidate

        if best_text == span.primary_text or best_gain < self.config.min_replacement_gain:
            return None
        return replace(span, replacement=best_text, replacement_score=best_gain, reason="bert_mlm")

    def _score_candidate(self, full_text: str, span: CandidateSpan, candidate: str) -> float | None:
        position = full_text.find(span.primary_text)
        if position < 0:
            return None
        before = full_text[:position]
        after = full_text[position + len(span.primary_text) :]
        left_budget = max(0, (self.max_context_chars - len(candidate)) // 2)
        right_budget = max(0, self.max_context_chars - len(candidate) - left_budget)
        left = before[-left_budget:] if left_budget else ""
        right = after[:right_budget] if right_budget else ""
        context = f"{left}{candidate}{right}"
        target_start = len(left)
        target_end = target_start + len(candidate)
        return self._masked_lm_score(context, target_start, target_end)

    def _masked_lm_score(self, text: str, target_start: int, target_end: int) -> float | None:
        try:
            encoded = self.tokenizer(
                text,
                return_tensors="pt",
                return_offsets_mapping=True,
                truncation=True,
                max_length=128,
            )
        except Exception:
            return self._fallback_whole_sequence_score(text)

        offsets = encoded.pop("offset_mapping")[0].tolist()
        input_ids = encoded["input_ids"].to(self.device)
        attention_mask = encoded.get("attention_mask")
        if attention_mask is not None:
            attention_mask = attention_mask.to(self.device)

        token_indices = []
        for index, (start, end) in enumerate(offsets):
            if end <= target_start or start >= target_end:
                continue
            token_id = int(input_ids[0, index].item())
            if token_id in self.tokenizer.all_special_ids:
                continue
            token_indices.append(index)
        if not token_indices:
            return None

        token_indices = token_indices[: self.max_scored_tokens]
        with self.torch.inference_mode():
            masked_batch = input_ids.repeat(len(token_indices), 1)
            targets = []
            for row, index in enumerate(token_indices):
                targets.append(int(masked_batch[row, index].item()))
                masked_batch[row, index] = self.tokenizer.mask_token_id
            attention_batch = attention_mask.repeat(len(token_indices), 1) if attention_mask is not None else None
            logits = self.model(input_ids=masked_batch, attention_mask=attention_batch).logits
            scores = []
            for row, index in enumerate(token_indices):
                log_probs = self.torch.log_softmax(logits[row, index], dim=-1)
                scores.append(float(log_probs[targets[row]].item()))
        return sum(scores) / max(1, len(scores))

    def _fallback_whole_sequence_score(self, text: str) -> float | None:
        try:
            encoded = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=128)
            input_ids = encoded["input_ids"].to(self.device)
            attention_mask = encoded.get("attention_mask")
            if attention_mask is not None:
                attention_mask = attention_mask.to(self.device)
            token_indices = [
                index
                for index, token_id in enumerate(input_ids[0].tolist())
                if token_id not in self.tokenizer.all_special_ids
            ][: self.max_scored_tokens]
            if not token_indices:
                return None
            with self.torch.inference_mode():
                masked_batch = input_ids.repeat(len(token_indices), 1)
                targets = []
                for row, index in enumerate(token_indices):
                    targets.append(int(masked_batch[row, index].item()))
                    masked_batch[row, index] = self.tokenizer.mask_token_id
                attention_batch = attention_mask.repeat(len(token_indices), 1) if attention_mask is not None else None
                logits = self.model(input_ids=masked_batch, attention_mask=attention_batch).logits
                scores = []
                for row, index in enumerate(token_indices):
                    log_probs = self.torch.log_softmax(logits[row, index], dim=-1)
                    scores.append(float(log_probs[targets[row]].item()))
            return sum(scores) / max(1, len(scores))
        except Exception:
            return None

    def _hotword_bonus(self, full_text: str, span: CandidateSpan, candidate: str) -> float:
        bonus = 0.0
        replaced = full_text.replace(span.primary_text, candidate, 1)
        for hotword in self.hotwords:
            if candidate == hotword or candidate in hotword or hotword in candidate:
                bonus += 0.6
            elif hotword in replaced and hotword not in full_text:
                bonus += 0.35
        return bonus
