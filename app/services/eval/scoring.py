"""Built-in evaluation metrics — no external ML dependencies."""

import math
import random
import re
import string
from collections import Counter


def _normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, strip, remove articles and punctuation."""
    text = text.lower().strip()
    # Remove articles
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    # Remove punctuation
    text = text.translate(str.maketrans("", "", string.punctuation))
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenize(text: str) -> list[str]:
    """Simple whitespace tokenizer."""
    return text.lower().split()


# ── Metrics ───────────────────────────────────────────────────────────────────


def accuracy(generated: str, expected: str) -> float:
    """Normalized exact match: 1.0 if normalized strings match, else 0.0."""
    return 1.0 if _normalize(generated) == _normalize(expected) else 0.0


def exact_match(generated: str, expected: str) -> float:
    """Strict string equality."""
    return 1.0 if generated.strip() == expected.strip() else 0.0


def f1_score(generated: str, expected: str) -> float:
    """Token-level F1 (precision/recall of word overlap)."""
    gen_tokens = Counter(_tokenize(generated))
    exp_tokens = Counter(_tokenize(expected))

    if not gen_tokens or not exp_tokens:
        return 1.0 if not gen_tokens and not exp_tokens else 0.0

    common = sum((gen_tokens & exp_tokens).values())
    if common == 0:
        return 0.0

    precision = common / sum(gen_tokens.values())
    recall = common / sum(exp_tokens.values())
    return 2 * precision * recall / (precision + recall)


def bleu_score(generated: str, expected: str, max_n: int = 4) -> float:
    """BLEU-4 score with simple tokenization (no nltk)."""
    gen_tokens = _tokenize(generated)
    ref_tokens = _tokenize(expected)

    if not gen_tokens or not ref_tokens:
        return 1.0 if not gen_tokens and not ref_tokens else 0.0

    # n-gram precisions
    precisions = []
    for n in range(1, max_n + 1):
        gen_ngrams = Counter(
            tuple(gen_tokens[i : i + n]) for i in range(len(gen_tokens) - n + 1)
        )
        ref_ngrams = Counter(
            tuple(ref_tokens[i : i + n]) for i in range(len(ref_tokens) - n + 1)
        )

        if not gen_ngrams:
            precisions.append(0.0)
            continue

        clipped = sum(min(gen_ngrams[ng], ref_ngrams.get(ng, 0)) for ng in gen_ngrams)
        precisions.append(clipped / sum(gen_ngrams.values()))

    # If any precision is zero, BLEU is zero
    if any(p == 0.0 for p in precisions):
        return 0.0

    # Geometric mean of precisions
    log_avg = sum(math.log(p) for p in precisions) / len(precisions)

    # Brevity penalty
    bp = 1.0
    if len(gen_tokens) < len(ref_tokens):
        bp = math.exp(1 - len(ref_tokens) / len(gen_tokens))

    return bp * math.exp(log_avg)


def rouge_l(generated: str, expected: str) -> float:
    """ROUGE-L: longest common subsequence F1."""
    gen_tokens = _tokenize(generated)
    ref_tokens = _tokenize(expected)

    if not gen_tokens or not ref_tokens:
        return 1.0 if not gen_tokens and not ref_tokens else 0.0

    # LCS length via DP
    m, n = len(ref_tokens), len(gen_tokens)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if ref_tokens[i - 1] == gen_tokens[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    lcs_len = dp[m][n]
    if lcs_len == 0:
        return 0.0

    precision = lcs_len / n
    recall = lcs_len / m
    return 2 * precision * recall / (precision + recall)


def perplexity_from_logprobs(logprobs: list[float]) -> float:
    """Compute perplexity from token log-probabilities."""
    if not logprobs:
        return float("inf")
    avg_log_prob = sum(logprobs) / len(logprobs)
    return math.exp(-avg_log_prob)


# ── Metric Registry ──────────────────────────────────────────────────────────

METRIC_FUNCTIONS: dict[str, callable] = {
    "accuracy": accuracy,
    "exact_match": exact_match,
    "f1": f1_score,
    "bleu": bleu_score,
    "rouge_l": rouge_l,
}


def score_example(
    generated: str,
    expected: str | None,
    metrics: list[str],
) -> dict[str, float]:
    """Score a single example across the requested metrics."""
    scores = {}
    for metric in metrics:
        fn = METRIC_FUNCTIONS.get(metric)
        if fn is None:
            continue
        if expected is None:
            # Can't compute comparison metrics without expected
            scores[metric] = 0.0
        else:
            scores[metric] = fn(generated, expected)
    return scores


# ── Confidence Intervals ──────────────────────────────────────────────────────


def bootstrap_ci(
    scores: list[float],
    n_resamples: int = 1000,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Bootstrap confidence interval.

    Returns (mean, ci_lower, ci_upper).
    """
    if not scores:
        return 0.0, 0.0, 0.0

    n = len(scores)
    if n == 1:
        return scores[0], scores[0], scores[0]

    rng = random.Random(42)  # deterministic for reproducibility
    means = []
    for _ in range(n_resamples):
        sample = rng.choices(scores, k=n)
        means.append(sum(sample) / len(sample))

    means.sort()
    alpha = (1 - confidence) / 2
    lo_idx = int(alpha * n_resamples)
    hi_idx = int((1 - alpha) * n_resamples) - 1

    mean = sum(scores) / n
    return mean, means[lo_idx], means[hi_idx]
