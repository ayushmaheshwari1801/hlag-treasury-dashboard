"""Every gateway model call is booked to the cost log with its stage and the provider-reported cost (ported from the
2026-11-08 team snapshot)."""
import json
import os
from pathlib import Path

import httpx

from treasury_gateway.assistant import Assistant, cost_stage, usage_request

CONFIG = {"base_url": "https://openrouter.ai/api/v1", "api_key": "test-key", "model": "m"}


def _transport(seen):
    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 120, "completion_tokens": 8, "cost": 0.00042}})
    return httpx.MockTransport(handle)


def test_calls_are_logged_with_stage_tokens_and_cost():
    seen = []
    a = Assistant(CONFIG, transport=_transport(seen))
    a._chat([{"role": "user", "content": "hi"}])
    with cost_stage("advice"), cost_stage("forecast"):      # the outer label wins
        a._chat([{"role": "user", "content": "hi"}])
    assert seen[0]["usage"] == {"include": True}
    lines = [json.loads(l) for l in Path(os.environ["LLM_COST_LOG"]).read_text().splitlines()]
    assert [(l["stage"], l["prompt_tokens"], l["completion_tokens"], l["cost_usd"]) for l in lines] == [
        ("assistant", 120, 8, 0.00042), ("advice", 120, 8, 0.00042)]
    assert "test-key" not in Path(os.environ["LLM_COST_LOG"]).read_text()


def test_only_openrouter_is_asked_for_usage():
    assert usage_request({"base_url": "https://api.example.com/v1"}) == {}
