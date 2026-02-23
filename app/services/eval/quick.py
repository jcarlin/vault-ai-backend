"""Quick eval service — runs inline evaluation without subprocess."""

import asyncio
import time

import httpx

from app.schemas.eval import (
    QuickEvalCaseResult,
    QuickEvalRequest,
    QuickEvalResponse,
)
from app.services.eval.scoring import METRIC_FUNCTIONS, score_example


async def _call_inference_async(
    http_client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model_id: str,
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 256,
    temperature: float = 0.0,
) -> str:
    """Call the API gateway asynchronously for a chat completion."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    response = await http_client.post(
        f"{base_url}/v1/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=120.0,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


async def run_quick_eval(
    request: QuickEvalRequest,
    api_base_url: str,
    api_key: str,
) -> QuickEvalResponse:
    """Run inline eval on up to 50 test cases. Returns results synchronously."""
    start_time = time.time()

    results: list[QuickEvalCaseResult] = []
    all_scores: dict[str, list[float]] = {
        m: [] for m in request.metrics if m in METRIC_FUNCTIONS
    }

    async with httpx.AsyncClient() as client:
        # Process in batches of 5 for concurrent API calls
        batch_size = 5
        for batch_start in range(0, len(request.test_cases), batch_size):
            batch = request.test_cases[batch_start : batch_start + batch_size]

            async def _eval_one(idx: int, case):
                try:
                    generated = await _call_inference_async(
                        http_client=client,
                        base_url=api_base_url,
                        api_key=api_key,
                        model_id=request.model_id,
                        prompt=case.prompt,
                        system_prompt=case.system_prompt,
                        max_tokens=request.max_tokens,
                        temperature=request.temperature,
                    )
                except Exception as e:
                    generated = f"[ERROR: {e}]"

                scores = score_example(generated, case.expected, request.metrics)

                correct = None
                if "accuracy" in scores:
                    correct = scores["accuracy"] == 1.0
                elif "exact_match" in scores:
                    correct = scores["exact_match"] == 1.0

                return QuickEvalCaseResult(
                    index=idx,
                    prompt=case.prompt,
                    expected=case.expected,
                    generated=generated,
                    scores=scores,
                    correct=correct,
                )

            tasks = [
                _eval_one(batch_start + i, case)
                for i, case in enumerate(batch)
            ]
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)

            for r in batch_results:
                for metric, score in r.scores.items():
                    if metric in all_scores:
                        all_scores[metric].append(score)

    # Compute aggregate scores
    aggregate = {}
    for metric, scores_list in all_scores.items():
        if scores_list:
            aggregate[metric] = round(sum(scores_list) / len(scores_list), 4)

    duration_ms = int((time.time() - start_time) * 1000)

    return QuickEvalResponse(
        results=results,
        aggregate_scores=aggregate,
        duration_ms=duration_ms,
    )
