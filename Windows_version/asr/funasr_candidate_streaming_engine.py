from __future__ import annotations

from .base import ASREvent
from .bert_reranker import DEFAULT_BERT_RERANKER_MODEL, BertMLMCandidateReranker
from .candidates import (
    CandidateConfig,
    SemanticCandidateReranker,
    build_candidate_spans,
    token_positions_from_topk,
)
from .funasr_offline_engine import extract_text
from .funasr_streaming_engine import DEFAULT_STREAMING_MODEL, SAMPLE_RATE, FunASRStreamingEngine
from server import log


class FunASRCandidateStreamingEngine(FunASRStreamingEngine):
    def __init__(
        self,
        model_name: str = DEFAULT_STREAMING_MODEL,
        hotwords: str = "",
        target_chunk_ms: int = 600,
        token_top_k: int = 5,
        semantic_reranker: str = "bert",
        semantic_model: str = DEFAULT_BERT_RERANKER_MODEL,
    ) -> None:
        super().__init__(model_name=model_name, hotwords=hotwords, target_chunk_ms=target_chunk_ms)
        self.enable_final_rescore = False
        self.candidate_config = CandidateConfig(token_top_k=token_top_k)
        self.semantic_reranker = semantic_reranker
        self.semantic_model = semantic_model
        self.reranker = self._create_reranker(hotwords)
        self.token_positions = []
        self._chunk_positions = []
        self._patched_decoder = False

    def _create_reranker(self, hotwords: str):
        if self.semantic_reranker == "bert":
            return BertMLMCandidateReranker(
                model_name=self.semantic_model,
                hotwords=hotwords,
                config=self.candidate_config,
            )
        return SemanticCandidateReranker(hotwords=hotwords, config=self.candidate_config)

    def start(self) -> None:
        try:
            from funasr import AutoModel

            try:
                self.model = AutoModel(model=self.model_name, disable_update=True)
            except TypeError:
                self.model = AutoModel(model=self.model_name)
        except Exception as exc:
            self.available = False
            self.unavailable_reason = str(exc)
            return

        self.final_model = None
        self._patch_decoder_topk_capture()
        start = getattr(self.reranker, "start", None)
        if callable(start):
            start()
            if not getattr(self.reranker, "available", True):
                log(
                    "[candidate] BERT reranker unavailable, fallback to heuristic: "
                    f"{getattr(self.reranker, 'unavailable_reason', '')}"
                )

    def _patch_decoder_topk_capture(self) -> None:
        if self.model is None or self._patched_decoder:
            return
        inner_model = getattr(self.model, "model", None)
        tokenizer = getattr(self.model, "kwargs", {}).get("tokenizer")
        if inner_model is None or tokenizer is None:
            self.unavailable_reason = "FunASR candidate mode cannot access inner model/tokenizer."
            return
        original = inner_model.cal_decoder_with_predictor_chunk

        def patched(*args, **kwargs):
            decoder_out, ys_pad_lens = original(*args, **kwargs)
            try:
                import torch

                top_values, top_ids = torch.topk(
                    decoder_out.detach().cpu(),
                    k=self.candidate_config.token_top_k,
                    dim=-1,
                )
                self._chunk_positions.extend(token_positions_from_topk(top_values, top_ids, tokenizer))
            except Exception as exc:
                log(f"[candidate] top-k capture failed: {exc}")
            return decoder_out, ys_pad_lens

        inner_model.cal_decoder_with_predictor_chunk = patched
        self._patched_decoder = True

    def _generate_streaming_chunk(self, pcm: bytes, is_final: bool) -> list[ASREvent]:
        self._chunk_positions = []
        events = super()._generate_streaming_chunk(pcm, is_final)
        if self._chunk_positions:
            offset = len(self.token_positions)
            for index, position in enumerate(self._chunk_positions):
                position.index = offset + index
            self.token_positions.extend(self._chunk_positions)
            spans = build_candidate_spans(self.token_positions, self.candidate_config)
            for event in events:
                event.candidate_spans = [span.to_dict() for span in spans]
                event.source = "candidate_partial"
        return events

    def finalize(self) -> list[ASREvent]:
        if not self.available:
            self.reset()
            return []
        if self.model is None:
            self.reset()
            return [ASREvent(type="error", text="", error="FunASR candidate streaming model is not started.")]

        if self.streaming_buffer:
            self._generate_streaming_chunk(bytes(self.streaming_buffer), is_final=False)
            self.streaming_buffer.clear()

        streaming_final = ""
        try:
            result = self._generate_with_compat(
                self.model,
                {
                    "input": [],
                    "cache": self.cache,
                    "chunk_size": self.chunk_size,
                    "is_final": True,
                    "fs": SAMPLE_RATE,
                },
            )
            streaming_final = extract_text(result)
        except Exception:
            streaming_final = ""

        immediate_text = streaming_final or self.last_partial
        with self.rescore_lock:
            self.utterance_id += 1
            utterance_id = self.utterance_id

        spans = build_candidate_spans(self.token_positions, self.candidate_config)
        reranked_spans = self.reranker.rerank(immediate_text, spans)
        corrected_text = apply_span_replacements(immediate_text, reranked_spans)
        if corrected_text != immediate_text:
            reranker_name = "bert_mlm" if self.semantic_reranker == "bert" else "heuristic"
            log(f"[candidate] {reranker_name} corrected {immediate_text!r} -> {corrected_text!r}")
        elif spans:
            log(f"[candidate] spans={len(spans)} no correction for {immediate_text!r}")

        event = ASREvent(
            type="final",
            text=corrected_text,
            source="candidate_streaming_final",
            utterance_id=utterance_id,
            candidate_spans=[span.to_dict() for span in spans],
        )
        self._clear_utterance_state()
        return [event] if corrected_text else []

    def _clear_utterance_state(self) -> None:
        super()._clear_utterance_state()
        self.token_positions = []
        self._chunk_positions = []


def apply_span_replacements(text: str, spans) -> str:
    result = text
    for span in spans:
        if not span.replacement or span.replacement == span.primary_text:
            continue
        position = result.find(span.primary_text)
        if position < 0:
            continue
        result = result[:position] + span.replacement + result[position + len(span.primary_text) :]
    return result
