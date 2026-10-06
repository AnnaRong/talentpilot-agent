"""Optional bounded analyst. Only pseudonymous numeric metrics leave the machine."""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import httpx

from .config import settings
from .workflows import analytics


def explain_metrics(capture, options, client=None):
    if os.getenv("LLM_EGRESS_ENABLED", "0") != "1" or not settings.llm_api_key:
        return {"status": "awaiting_configuration", "llm_calls": 0,
                "missing": ["Approved HTTPS model gateway, API key and LLM_EGRESS_ENABLED=1"],
                "summary": [], "verified": False}
    url = urlsplit(settings.llm_base_url)
    if url.scheme != "https" or url.hostname != os.getenv("LLM_ALLOWED_HOST", "") or url.username or url.password:
        raise ValueError("Model endpoint must match the administrator's approved HTTPS host")
    metrics = {}
    for index, row in enumerate(analytics(capture)["jobs"]):
        for key in ("applications", "completed_explicit", "passed", "rejected", "pass_rate"):
            metrics[f"job_{index+1}.{key}"] = row[key]
    messages = [
        {"role": "system", "content": (
            "You are a recruiting METRICS analyst, not a hiring decision maker. "
            "Only call lookup_metric or finish with a JSON object. "
            "Never infer reasons, candidate fitness, bias, historical conversion or sent Offers. "
            'Tool request: {"tool":"lookup_metric","metric_id":"..."} . '
            'Final: {"summary":[{"metric_id":"...","value":0,"interpretation":"..."}]}. '
            "Every value must exactly match a tool result. At most 4 calls including final. "
            "Interpretations are human-review drafts, never approved decisions."
        )},
        {"role": "user", "content": json.dumps({"available_metrics": list(metrics)[:500]})},
    ]
    observed, total_tokens = {}, 0
    own_client = client is None
    client = client or httpx.Client(timeout=8, follow_redirects=False)
    try:
        for call in range(1, 5):
            response = client.post(
                settings.llm_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + settings.llm_api_key},
                json={"model": settings.llm_model, "messages": messages,
                      "max_completion_tokens": 800, "response_format": {"type": "json_object"}},
            )
            if response.status_code != 200:
                raise ValueError(f"Model gateway HTTP {response.status_code}; no fallback success")
            body = response.json()
            text = body["choices"][0]["message"]["content"]
            value = json.loads(text)
            total_tokens += int(body.get("usage", {}).get("total_tokens", 0))
            if total_tokens > 12000:
                raise ValueError("Model reported-token budget exceeded")
            if "tool" in value:
                metric_id = value.get("metric_id")
                if value["tool"] != "lookup_metric" or metric_id not in metrics:
                    raise ValueError("Model requested a forbidden tool or unknown metric")
                observed[metric_id] = metrics[metric_id]
                messages.extend([
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": json.dumps({"tool_result": {
                        "metric_id": metric_id, "value": metrics[metric_id]}})},
                ])
                continue
            summary = value.get("summary")
            if not isinstance(summary, list) or not summary or len(summary) > 10:
                raise ValueError("Model did not return a bounded sourced summary")
            for claim in summary:
                if (not isinstance(claim, dict) or claim.get("metric_id") not in observed or
                    "value" not in claim or claim.get("value") != observed[claim["metric_id"]] or
                    type(claim.get("value")) is not type(observed[claim["metric_id"]]) or
                    not isinstance(claim.get("interpretation"), str) or len(claim["interpretation"]) > 1000):
                    raise ValueError("Evaluator rejected unsourced or inconsistent model claim")
            return {"status": "draft_for_human_review", "model": settings.llm_model,
                    "llm_calls": call, "reported_tokens": total_tokens, "summary": summary,
                    "numeric_citations_verified": True, "narrative_verified": False,
                    "external_hiring_actions": 0}
        raise ValueError("Model tool-loop budget exhausted")
    finally:
        if own_client:
            client.close()
