import unittest

from asr.candidates import (
    CandidateConfig,
    CandidateSpan,
    SemanticCandidateReranker,
    TokenCandidate,
    TokenPosition,
    build_candidate_spans,
)
from asr.bert_reranker import BertMLMCandidateReranker
from asr.funasr_candidate_streaming_engine import apply_span_replacements


def position(index, primary, alternatives=None, top1_logprob=-0.1, margin=5.0):
    alternatives = alternatives or []
    candidates = [TokenCandidate(primary, index, top1_logprob)]
    candidates.extend(TokenCandidate(token, index + offset + 1, logprob) for offset, (token, logprob) in enumerate(alternatives))
    confidence = 0.9 if margin >= 4.0 and top1_logprob >= -0.5 else 0.2
    return TokenPosition(
        index=index,
        primary=primary,
        candidates=candidates,
        top1_logprob=top1_logprob,
        margin=margin,
        confidence=confidence,
    )


class CandidateTests(unittest.TestCase):
    def test_build_candidate_spans_only_targets_low_confidence_region(self):
        positions = [
            position(0, "欢"),
            position(1, "迎"),
            position(2, "达", [("大", -0.9)], top1_logprob=-1.2, margin=0.3),
            position(3, "摩", [("模", -0.8)], top1_logprob=-1.1, margin=0.4),
            position(4, "院"),
        ]

        spans = build_candidate_spans(positions, CandidateConfig(max_span_tokens=4))

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].primary_text, "迎达摩院")
        self.assertIn("迎达摩院", spans[0].candidates)

    def test_hotword_reranker_selects_candidate_inside_candidate_space(self):
        span = CandidateSpan(
            start=0,
            end=3,
            primary_text="达模型",
            candidates=["达模型", "达摩院"],
            acoustic_score=-0.8,
            confidence=0.3,
        )

        reranked = SemanticCandidateReranker(hotwords="达摩院").rerank("欢迎体验达模型", [span])

        self.assertEqual(len(reranked), 1)
        self.assertEqual(reranked[0].replacement, "达摩院")

    def test_apply_span_replacements_uses_safe_candidate_replacement(self):
        span = CandidateSpan(
            start=0,
            end=3,
            primary_text="达模型",
            candidates=["达模型", "达摩院"],
            acoustic_score=-0.8,
            confidence=0.3,
            replacement="达摩院",
            replacement_score=1.0,
        )

        corrected = apply_span_replacements("我想体验达模型", [span])

        self.assertEqual(corrected, "我想体验达摩院")

    def test_bert_reranker_is_candidate_constrained(self):
        class FakeBertReranker(BertMLMCandidateReranker):
            def start(self):
                self.available = True
                self.model = object()
                self.tokenizer = object()
                self.torch = object()

            def _score_candidate(self, full_text, span, candidate):
                return {"达模型": -4.0, "达摩院": -1.0}.get(candidate, -10.0)

        span = CandidateSpan(
            start=0,
            end=3,
            primary_text="达模型",
            candidates=["达模型", "达摩院"],
            acoustic_score=-0.8,
            confidence=0.3,
        )
        reranker = FakeBertReranker()
        reranker.start()

        reranked = reranker.rerank("欢迎体验达模型", [span])

        self.assertEqual(len(reranked), 1)
        self.assertEqual(reranked[0].replacement, "达摩院")

    def test_bert_reranker_falls_back_when_model_unavailable(self):
        span = CandidateSpan(
            start=0,
            end=3,
            primary_text="达模型",
            candidates=["达模型", "达摩院"],
            acoustic_score=-0.8,
            confidence=0.3,
        )
        reranker = BertMLMCandidateReranker(hotwords="达摩院")

        reranked = reranker.rerank("欢迎体验达模型", [span])

        self.assertEqual(len(reranked), 1)
        self.assertEqual(reranked[0].replacement, "达摩院")


if __name__ == "__main__":
    unittest.main()
