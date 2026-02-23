"""Unit tests for eval scoring module."""

import pytest

from app.services.eval.scoring import (
    accuracy,
    bleu_score,
    bootstrap_ci,
    exact_match,
    f1_score,
    rouge_l,
    score_example,
)


class TestAccuracy:
    def test_exact_match(self):
        assert accuracy("Paris", "Paris") == 1.0

    def test_case_insensitive(self):
        assert accuracy("paris", "Paris") == 1.0

    def test_strips_whitespace(self):
        assert accuracy("  Paris  ", "Paris") == 1.0

    def test_removes_articles(self):
        assert accuracy("The capital is Paris", "capital is Paris") == 1.0

    def test_no_match(self):
        assert accuracy("London", "Paris") == 0.0


class TestExactMatch:
    def test_match(self):
        assert exact_match("Paris", "Paris") == 1.0

    def test_case_sensitive(self):
        assert exact_match("paris", "Paris") == 0.0

    def test_strips_whitespace(self):
        assert exact_match("  Paris  ", "Paris") == 1.0


class TestF1:
    def test_identical(self):
        assert f1_score("the quick brown fox", "the quick brown fox") == 1.0

    def test_partial_overlap(self):
        score = f1_score("the quick brown fox", "the slow brown cat")
        assert 0.0 < score < 1.0

    def test_no_overlap(self):
        assert f1_score("hello world", "foo bar") == 0.0

    def test_empty_both(self):
        assert f1_score("", "") == 1.0

    def test_empty_one(self):
        assert f1_score("", "hello") == 0.0


class TestBleu:
    def test_identical(self):
        score = bleu_score("the cat sat on the mat", "the cat sat on the mat")
        assert score == 1.0

    def test_no_overlap(self):
        score = bleu_score("hello world foo bar", "completely different text here")
        assert score == 0.0

    def test_partial(self):
        score = bleu_score("the cat sat on the mat", "the cat sat on a mat")
        assert 0.0 < score < 1.0


class TestRougeL:
    def test_identical(self):
        assert rouge_l("the quick brown fox", "the quick brown fox") == 1.0

    def test_subsequence(self):
        score = rouge_l("the brown fox", "the quick brown fox")
        assert 0.0 < score <= 1.0

    def test_no_overlap(self):
        assert rouge_l("hello world", "foo bar") == 0.0


class TestScoreExample:
    def test_single_metric(self):
        scores = score_example("Paris", "Paris", ["accuracy"])
        assert scores == {"accuracy": 1.0}

    def test_multiple_metrics(self):
        scores = score_example("Paris", "Paris", ["accuracy", "exact_match", "f1"])
        assert scores["accuracy"] == 1.0
        assert scores["exact_match"] == 1.0
        assert scores["f1"] == 1.0

    def test_unknown_metric_skipped(self):
        scores = score_example("Paris", "Paris", ["accuracy", "nonexistent"])
        assert "accuracy" in scores
        assert "nonexistent" not in scores

    def test_no_expected(self):
        scores = score_example("Paris", None, ["accuracy"])
        assert scores["accuracy"] == 0.0


class TestBootstrapCI:
    def test_basic(self):
        scores = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0]
        mean, ci_lo, ci_hi = bootstrap_ci(scores)
        assert 0.5 < mean < 1.0
        assert ci_lo <= mean <= ci_hi

    def test_single_value(self):
        mean, ci_lo, ci_hi = bootstrap_ci([1.0])
        assert mean == 1.0
        assert ci_lo == 1.0
        assert ci_hi == 1.0

    def test_empty(self):
        mean, ci_lo, ci_hi = bootstrap_ci([])
        assert mean == 0.0

    def test_all_same(self):
        mean, ci_lo, ci_hi = bootstrap_ci([0.5] * 100)
        assert mean == 0.5
        assert ci_lo == 0.5
        assert ci_hi == 0.5
