from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, field


SPECIAL_TOKENS = {"<blank>", "<s>", "</s>", "<unk>", "<pad>"}


@dataclass
class TokenCandidate:
    token: str
    token_id: int
    logprob: float


@dataclass
class TokenPosition:
    index: int
    primary: str
    candidates: list[TokenCandidate]
    top1_logprob: float
    margin: float
    confidence: float


@dataclass
class CandidateSpan:
    start: int
    end: int
    primary_text: str
    candidates: list[str]
    acoustic_score: float
    confidence: float
    reason: str = ""
    replacement: str = ""
    replacement_score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CandidateConfig:
    token_top_k: int = 5
    high_conf_margin: float = 4.0
    high_conf_logprob: float = -0.5
    low_conf_margin: float = 3.0
    low_conf_logprob: float = -1.0
    max_span_tokens: int = 6
    max_spans: int = 6
    max_candidates_per_span: int = 8
    min_replacement_gain: float = 0.35


def normalize_token(token: str) -> str:
    if token.endswith("@@"):
        return token[:-2]
    return token


def token_positions_from_topk(top_values, top_ids, tokenizer) -> list[TokenPosition]:
    positions: list[TokenPosition] = []
    for index in range(top_ids.shape[1]):
        ids = top_ids[0, index].tolist()
        values = top_values[0, index].tolist()
        tokens = tokenizer.ids2tokens(ids)
        candidates: list[TokenCandidate] = []
        for token_id, token, logprob in zip(ids, tokens, values):
            normalized = normalize_token(str(token))
            if not normalized or normalized in SPECIAL_TOKENS:
                continue
            candidates.append(TokenCandidate(token=normalized, token_id=int(token_id), logprob=float(logprob)))
        if not candidates:
            continue
        top1 = candidates[0].logprob
        top2 = candidates[1].logprob if len(candidates) > 1 else -100.0
        margin = top1 - top2
        confidence = token_confidence(top1, margin)
        positions.append(
            TokenPosition(
                index=index,
                primary=candidates[0].token,
                candidates=candidates,
                top1_logprob=top1,
                margin=margin,
                confidence=confidence,
            )
        )
    return positions


def token_confidence(top1_logprob: float, margin: float) -> float:
    margin_score = max(0.0, min(1.0, margin / 6.0))
    prob_score = max(0.0, min(1.0, math.exp(max(-20.0, top1_logprob))))
    return 0.65 * margin_score + 0.35 * prob_score


def build_candidate_spans(
    positions: list[TokenPosition],
    config: CandidateConfig | None = None,
) -> list[CandidateSpan]:
    cfg = config or CandidateConfig()
    spans: list[CandidateSpan] = []
    index = 0
    while index < len(positions):
        position = positions[index]
        if not is_low_confidence(position, cfg):
            index += 1
            continue

        start = index
        end = index + 1
        while end < len(positions) and is_low_confidence(positions[end], cfg):
            end += 1
        if start > 0:
            start -= 1
        if end < len(positions):
            end += 1
        while end - start > cfg.max_span_tokens:
            if is_low_confidence(positions[start], cfg):
                end -= 1
            else:
                start += 1

        span_positions = positions[start:end]
        primary_text = "".join(pos.primary for pos in span_positions)
        candidates = generate_span_candidates(span_positions, cfg)
        if candidates and candidates[0] == primary_text:
            candidate_texts = candidates
        else:
            candidate_texts = [primary_text] + [item for item in candidates if item != primary_text]

        spans.append(
            CandidateSpan(
                start=span_positions[0].index,
                end=span_positions[-1].index + 1,
                primary_text=primary_text,
                candidates=candidate_texts[: cfg.max_candidates_per_span],
                acoustic_score=sum(pos.top1_logprob for pos in span_positions) / max(1, len(span_positions)),
                confidence=sum(pos.confidence for pos in span_positions) / max(1, len(span_positions)),
                reason="low_confidence",
            )
        )
        index = max(end, index + 1)
        if len(spans) >= cfg.max_spans:
            break
    return spans


def is_low_confidence(position: TokenPosition, cfg: CandidateConfig) -> bool:
    if position.margin >= cfg.high_conf_margin and position.top1_logprob >= cfg.high_conf_logprob:
        return False
    return position.margin < cfg.low_conf_margin or position.top1_logprob < cfg.low_conf_logprob


def generate_span_candidates(positions: list[TokenPosition], cfg: CandidateConfig) -> list[str]:
    candidate_lists = []
    for position in positions:
        usable = [candidate for candidate in position.candidates if candidate.token and candidate.token not in SPECIAL_TOKENS]
        candidate_lists.append(usable[: cfg.token_top_k])

    scored: list[tuple[float, str]] = []
    for combo in itertools.product(*candidate_lists):
        text = "".join(candidate.token for candidate in combo)
        if not text:
            continue
        score = sum(candidate.logprob for candidate in combo) / max(1, len(combo))
        scored.append((score, text))

    dedup: dict[str, float] = {}
    for score, text in scored:
        if text not in dedup or score > dedup[text]:
            dedup[text] = score

    return [
        text
        for text, _score in sorted(dedup.items(), key=lambda item: item[1], reverse=True)[
            : cfg.max_candidates_per_span
        ]
    ]


class SemanticCandidateReranker:
    def __init__(self, hotwords: str = "", config: CandidateConfig | None = None) -> None:
        self.hotwords = [line.strip() for line in hotwords.splitlines() if line.strip()]
        self.config = config or CandidateConfig()

    def rerank(self, full_text: str, spans: list[CandidateSpan]) -> list[CandidateSpan]:
        reranked: list[CandidateSpan] = []
        for span in spans:
            best_text = span.primary_text
            best_score = 0.0
            for candidate in span.candidates:
                score = self._score_candidate(full_text, span, candidate)
                if score > best_score:
                    best_score = score
                    best_text = candidate
            if best_text != span.primary_text and best_score >= self.config.min_replacement_gain:
                span.replacement = best_text
                span.replacement_score = best_score
                reranked.append(span)
        return reranked

    def _score_candidate(self, full_text: str, span: CandidateSpan, candidate: str) -> float:
        score = 0.0
        for hotword in self.hotwords:
            if not hotword:
                continue
            if candidate == hotword or candidate in hotword or hotword in candidate:
                score += 1.0
            elif hotword in full_text.replace(span.primary_text, candidate, 1):
                score += 0.5
        if candidate == span.primary_text:
            score -= 0.1
        if len(candidate) != len(span.primary_text):
            score -= 0.05 * abs(len(candidate) - len(span.primary_text))
        return score
