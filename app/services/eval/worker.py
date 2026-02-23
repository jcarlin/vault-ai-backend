"""Eval worker — standalone subprocess that runs evaluation.

Usage: python -m app.services.eval.worker --config /path/to/config.json
"""

import argparse
import json
import signal
import sys
import time
from pathlib import Path

import httpx

from app.services.eval.config import EvalRunConfig
from app.services.eval.scoring import METRIC_FUNCTIONS, bootstrap_ci, score_example


_cancel_requested = False


def _handle_sigterm(signum, frame):
    global _cancel_requested
    _cancel_requested = True


signal.signal(signal.SIGTERM, _handle_sigterm)


def _load_dataset(path: str, num_samples: int | None = None) -> list[dict]:
    """Load a JSONL dataset file."""
    examples = []
    dataset_path = Path(path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    with open(dataset_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            examples.append(json.loads(line))

    if num_samples and num_samples < len(examples):
        examples = examples[:num_samples]

    return examples


def _write_status(status_dir: str, data: dict) -> None:
    """Write status.json atomically."""
    status_path = Path(status_dir) / "status.json"
    tmp_path = status_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data))
    tmp_path.rename(status_path)


def _call_inference(
    client: httpx.Client,
    api_base_url: str,
    api_key: str,
    model_id: str,
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 256,
    temperature: float = 0.0,
    few_shot_examples: list[dict] | None = None,
) -> str:
    """Call the API gateway for a chat completion."""
    messages = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if few_shot_examples:
        for ex in few_shot_examples:
            messages.append({"role": "user", "content": ex["prompt"]})
            messages.append({"role": "assistant", "content": ex.get("expected", "")})

    messages.append({"role": "user", "content": prompt})

    url = f"{api_base_url}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    response = client.post(url, json=payload, headers=headers, timeout=120.0)
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"]


def run_eval(config: EvalRunConfig) -> None:
    """Main evaluation loop."""
    global _cancel_requested

    start_time = time.time()

    # Load dataset
    examples = _load_dataset(config.dataset_path, config.num_samples)
    total = len(examples)

    if total == 0:
        _write_status(config.status_dir, {
            "error": "Dataset is empty",
            "state": "failed",
        })
        sys.exit(1)

    # Prepare few-shot examples (take from beginning, eval from the rest)
    few_shot_examples = []
    eval_examples = examples
    if config.few_shot > 0 and len(examples) > config.few_shot:
        few_shot_examples = examples[: config.few_shot]
        eval_examples = examples[config.few_shot :]
        total = len(eval_examples)

    # Initialize per-metric score accumulators
    all_scores: dict[str, list[float]] = {m: [] for m in config.metrics if m in METRIC_FUNCTIONS}
    per_example_results = []

    client = httpx.Client()

    try:
        for batch_start in range(0, total, config.batch_size):
            if _cancel_requested:
                break

            batch_end = min(batch_start + config.batch_size, total)
            batch = eval_examples[batch_start:batch_end]

            for i, example in enumerate(batch):
                if _cancel_requested:
                    break

                idx = batch_start + i
                prompt = example.get("prompt", "")
                expected = example.get("expected")
                system_prompt = example.get("system_prompt")

                try:
                    generated = _call_inference(
                        client=client,
                        api_base_url=config.api_base_url,
                        api_key=config.api_key,
                        model_id=config.model_id,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        max_tokens=config.max_tokens,
                        temperature=config.temperature,
                        few_shot_examples=few_shot_examples,
                    )
                except Exception as e:
                    generated = f"[ERROR: {e}]"

                scores = score_example(generated, expected, config.metrics)

                # Determine correctness
                correct = None
                if "accuracy" in scores:
                    correct = scores["accuracy"] == 1.0
                elif "exact_match" in scores:
                    correct = scores["exact_match"] == 1.0

                per_example_results.append({
                    "index": idx,
                    "prompt": prompt[:500],
                    "expected": expected[:500] if expected else None,
                    "generated": generated[:500],
                    "scores": scores,
                    "correct": correct,
                })

                for metric, score in scores.items():
                    if metric in all_scores:
                        all_scores[metric].append(score)

            # Write progress
            completed = min(batch_end, total) if not _cancel_requested else batch_start + i
            elapsed = time.time() - start_time
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (total - completed) / rate if rate > 0 else 0

            current_scores = {}
            for metric, scores_list in all_scores.items():
                if scores_list:
                    current_scores[metric] = sum(scores_list) / len(scores_list)

            _write_status(config.status_dir, {
                "state": "running",
                "examples_completed": completed,
                "total_examples": total,
                "current_scores": current_scores,
                "eta_seconds": round(eta, 1),
            })

    finally:
        client.close()

    if _cancel_requested:
        _write_status(config.status_dir, {"state": "cancelled"})
        sys.exit(143)

    # Compute final metrics with confidence intervals
    metric_results = []
    for metric, scores_list in all_scores.items():
        if scores_list:
            mean, ci_lower, ci_upper = bootstrap_ci(scores_list)
            metric_results.append({
                "metric": metric,
                "score": round(mean, 4),
                "ci_lower": round(ci_lower, 4),
                "ci_upper": round(ci_upper, 4),
            })

    # Build summary string
    summary_parts = []
    for mr in metric_results:
        summary_parts.append(f"{mr['metric']}: {mr['score']:.2%}")
    summary = " | ".join(summary_parts) if summary_parts else "No metrics computed"

    final_results = {
        "metrics": metric_results,
        "per_example": per_example_results,
        "summary": summary,
    }

    _write_status(config.status_dir, {
        "state": "completed",
        "examples_completed": total,
        "total_examples": total,
        "results": final_results,
    })


def main():
    parser = argparse.ArgumentParser(description="Vault AI Eval Worker")
    parser.add_argument("--config", required=True, help="Path to eval config JSON")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    config = EvalRunConfig(**json.loads(config_path.read_text()))

    try:
        run_eval(config)
    except Exception as e:
        _write_status(config.status_dir, {
            "state": "failed",
            "error": str(e),
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
