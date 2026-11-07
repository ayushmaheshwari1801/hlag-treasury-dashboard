"""Exa search and OpenAI-compatible structured output, without hidden retries."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re
from threading import Event, Lock
from time import perf_counter
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from .config import validate_base_url
from .id_constraints import IDConstraintError, validation_error


def _strict_schema(value):
    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _strict_schema(item) for key, item in value.items() if key != "default"}
    if result.get("type") == "object" or "properties" in result:
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


def _check_duplicate_json_keys(content, schema, stage):
    duplicate_ids, duplicate_fields = set(), set()
    unknown_duplicate = False
    known_fields = set()
    def fields(node):
        if isinstance(node, dict):
            known_fields.update(node.get("properties", {}))
            for value in node.values():
                fields(value)
        elif isinstance(node, list):
            for value in node:
                fields(value)
    fields(schema)
    def pairs(items):
        nonlocal unknown_duplicate
        result = {}
        for key, value in items:
            if key in result:
                if re.fullmatch(r"[AEDP][A-Za-z0-9_-]{0,127}", key):
                    duplicate_ids.add(key)
                elif key in known_fields:
                    duplicate_fields.add(key)
                else:
                    unknown_duplicate = True
            result[key] = value
        return result
    json.loads(content, object_pairs_hook=pairs)
    if duplicate_ids or duplicate_fields or unknown_duplicate:
        raise IDConstraintError("duplicate_json_keys", duplicate_ids=duplicate_ids,
            stage=stage, duplicate_fields=sorted(duplicate_fields),
            unknown_duplicate_fields=unknown_duplicate)


def _key(value):
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError("Provider API key must be nonempty and contain no whitespace")
    return value


def _known_cost(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _billing_error(error):
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    if isinstance(code, (int, str)) and code in {
        402, "402", "insufficient_quota", "insufficient_credits", "out_of_credit",
    }:
        return True
    metadata = error.get("metadata", {})
    return (code in (403, "403") and isinstance(metadata, dict)
            and (metadata.get("limit_source") == "openrouter_key_limit"
                 or metadata.get("scope") == "key_limit"))


class LLMClient:
    def __init__(self, api_key: str, model: str, base_url: str, *,
                 http_client: httpx.Client | None = None):
        self._key = _key(api_key)
        if not isinstance(model, str) or not model.strip():
            raise ValueError("LLM model must be configured")
        self.model = model.strip()
        self.base_url = validate_base_url(base_url)
        self._http = http_client or httpx.Client(timeout=90, follow_redirects=False)
        self._owned = http_client is None
        self._lock = Lock()
        self._stopped = Event()
        self._receipts = []
        self._validation_errors = []

    @property
    def receipts(self):
        with self._lock:
            return deepcopy(self._receipts)

    @property
    def validation_errors(self):
        with self._lock:
            return deepcopy(self._validation_errors)

    def record_validation_error(self, details, stage):
        # Semantic checks run after provider parsing; retain diagnostics without a billed call.
        record = {**deepcopy(details), "stage": stage}
        with self._lock:
            self._validation_errors.append(record)

    def close(self):
        if self._owned:
            self._http.close()

    def complete(self, response_model: type[BaseModel], system_prompt: str,
                 context: dict, *, stage: str) -> BaseModel:
        if self._stopped.is_set():
            raise RuntimeError("Provider authentication or credit failure; further requests stopped")
        schema = _strict_schema(response_model.model_json_schema())
        system_prompt += (
            "\n\nOUTPUT FIELD CONTRACT\n"
            "Return only one JSON object conforming to this schema. Use exact field names "
            "and enum spellings; include every required key and no extra keys. "
            "Use JSON null only where allowed, never the strings 'null' or 'None'. "
            "Runtime reference IDs must come from the supplied allowed values/candidates.\n"
            + json.dumps(schema, ensure_ascii=False, separators=(',', ':'))
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": json.dumps(context, ensure_ascii=False, allow_nan=False)}],
            "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": response_model.__name__, "strict": True,
                "schema": schema,
            }},
        }
        if urlsplit(self.base_url).hostname == "openrouter.ai":
            payload.update(reasoning={"enabled": False}, provider={
                "require_parameters": True, "allow_fallbacks": True, "data_collection": "deny",
            })
        receipt = {"service": "llm", "stage": stage, "status": "failed", "model": self.model}
        started = perf_counter()
        try:
            response = self._http.post(self.base_url + "/chat/completions",
                headers={"Authorization": "Bearer " + self._key}, json=payload)
            receipt["http_status"] = response.status_code
            if not 200 <= response.status_code < 300:
                billing = False
                try:
                    error = response.json().get("error", {})
                    billing = _billing_error(error)
                except (ValueError, AttributeError):
                    pass
                if response.status_code in {401, 402, 403} or billing:
                    self._stopped.set()
                raise RuntimeError(f"LLM request failed (HTTP {response.status_code})")
            try:
                result = response.json()
                if _billing_error(result.get("error")):
                    self._stopped.set()
                    raise RuntimeError("LLM credit failure; further requests stopped")
                usage = result.get("usage", {})
                for name in ("prompt_tokens", "completion_tokens"):
                    if type(usage.get(name)) is int and usage[name] >= 0:
                        receipt[name] = usage[name]
                if _known_cost(usage.get("cost")):
                    receipt["cost_usd"] = usage["cost"]
                choices = result.get("choices", [])
                if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
                    raise ValueError("Incomplete LLM response")
                message = choices[0]["message"]
                if message.get("refusal") or message.get("tool_calls"):
                    raise ValueError("Unexpected LLM response")
                _check_duplicate_json_keys(message["content"], schema, stage)
                parsed = response_model.model_validate_json(message["content"])
            except ValidationError as exc:
                error = validation_error(exc, stage=stage,
                    constraints=getattr(response_model, "__id_constraints__", None))
                if error is not None:
                    receipt["validation_error"] = error.details
                    raise error from None
                details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['type']}"
                                    for e in exc.errors(include_input=False)[:8])
                raise ValueError("Structured output validation failed: " + details) from None
            except IDConstraintError as exc:
                receipt["validation_error"] = exc.details
                raise
            except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                raise ValueError("LLM returned invalid or incomplete structured output") from None
            receipt["status"] = "succeeded"
            return parsed
        except httpx.HTTPError:
            raise RuntimeError("LLM transport failed") from None
        finally:
            receipt["latency_seconds"] = perf_counter() - started
            with self._lock:
                self._receipts.append(receipt)


class ExaClient:
    def __init__(self, api_key: str, *, http_client: httpx.Client | None = None,
                 max_results_per_query: int = 10):
        self._key = _key(api_key)
        if type(max_results_per_query) is not int or not 1 <= max_results_per_query <= 10:
            raise ValueError("Exa result limit must be 1..10")
        self._limit = max_results_per_query
        self._http = http_client or httpx.Client(timeout=60, follow_redirects=False)
        self._owned = http_client is None
        self._receipts = []
        self._stopped = False

    @property
    def receipts(self):
        return deepcopy(self._receipts)

    def close(self):
        if self._owned:
            self._http.close()

    def search(self, query: str, *, start: datetime | None, end: datetime):
        if self._stopped:
            raise RuntimeError("Exa authentication or credit failure; further requests stopped")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Search query must be nonempty")
        if end.tzinfo is None or (start is not None and (start.tzinfo is None or start > end)):
            raise ValueError("Search dates must be timezone-aware and ordered")
        payload = {"query": query, "type": "auto", "numResults": self._limit,
                   "contents": {"text": True}, "endPublishedDate": end.astimezone(timezone.utc).isoformat()}
        if start is not None:
            payload["startPublishedDate"] = start.astimezone(timezone.utc).isoformat()
        receipt = {"service": "exa", "stage": "search", "status": "failed"}
        started = perf_counter()
        try:
            response = self._http.post("https://api.exa.ai/search",
                                      headers={"x-api-key": self._key}, json=payload)
            receipt["http_status"] = response.status_code
            if not 200 <= response.status_code < 300:
                self._stopped = response.status_code in {401, 402, 403}
                raise RuntimeError(f"Exa request failed (HTTP {response.status_code})")
            result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get("results"), list):
                raise ValueError("Exa response requires results")
            cost = result.get("costDollars", {}).get("total")
            if _known_cost(cost):
                receipt["cost_usd"] = cost
            receipt["status"] = "succeeded"
            return result
        except httpx.HTTPError:
            raise RuntimeError("Exa transport failed") from None
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("Exa returned invalid structured output") from None
        finally:
            receipt["latency_seconds"] = perf_counter() - started
            self._receipts.append(receipt)
