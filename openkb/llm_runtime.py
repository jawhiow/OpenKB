from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import litellm
from agents import set_default_openai_api, set_default_openai_client
from agents.model_settings import ModelSettings
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, OpenAI
from openai.types.shared import Reasoning


@dataclass
class CompletionResult:
    text: str
    usage: Any


_async_client: AsyncOpenAI | None = None
_async_client_config: tuple[str | None, str | None] | None = None
_sync_client: OpenAI | None = None
_sync_client_config: tuple[str | None, str | None] | None = None
_agents_configured: tuple[str | None, str | None, str] | None = None


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "")
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _configured_wire_api() -> str:
    return os.getenv("OPENKB_WIRE_API", os.getenv("OPENAI_WIRE_API", "chat_completions")).strip().lower()


def model_prefers_responses_api(model: str | None) -> bool:
    if not model:
        return False
    normalized = model.strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    return normalized.startswith("gpt-5")


def get_wire_api(model: str | None = None) -> str:
    configured = _configured_wire_api()
    if configured == "chat_completions" and model_prefers_responses_api(model):
        return "responses"
    return configured


def uses_responses_api(model: str | None = None) -> bool:
    return get_wire_api(model) == "responses"


def get_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")


def get_base_url(model: str | None = None) -> str | None:
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
    if not base_url:
        return None
    base_url = base_url.rstrip("/")
    if uses_responses_api(model) and not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return base_url


def get_reasoning_effort() -> str | None:
    value = os.getenv("OPENKB_MODEL_REASONING_EFFORT", "").strip().lower()
    return value or None


def get_verbosity() -> str | None:
    value = os.getenv("OPENKB_MODEL_VERBOSITY", "").strip().lower()
    return value or None


def disable_response_storage() -> bool:
    return _env_flag("OPENKB_DISABLE_RESPONSE_STORAGE", default=False)


def get_response_max_retries() -> int:
    value = os.getenv("OPENKB_RESPONSE_MAX_RETRIES", "").strip()
    if not value:
        return 2
    try:
        return max(int(value), 0)
    except ValueError:
        return 2


def get_response_retry_max_delay() -> float:
    value = os.getenv("OPENKB_RESPONSE_RETRY_MAX_DELAY", "").strip()
    if not value:
        return 10.0
    try:
        return max(float(value), 0.0)
    except ValueError:
        return 10.0


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _error_body(exc: Exception) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    return body if isinstance(body, dict) else {}


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        retry_after = headers.get("retry-after")
        if retry_after is not None:
            try:
                return max(float(retry_after), 0.0)
            except (TypeError, ValueError):
                pass

    retry_after = _error_body(exc).get("retry_after")
    if retry_after is not None:
        try:
            return max(float(retry_after), 0.0)
        except (TypeError, ValueError):
            return None
    return None


def _is_retryable_responses_error(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True

    body = _error_body(exc)
    if body.get("retryable") is True:
        return True

    status = _status_code(exc)
    return status in {408, 409, 429} or (status is not None and status >= 500)


def _retry_delay_seconds(attempt: int, exc: Exception) -> float:
    max_delay = get_response_retry_max_delay()
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, max_delay)
    return min(float(2 ** attempt), max_delay)


def _get_sync_openai_client(model: str | None = None) -> OpenAI:
    global _sync_client, _sync_client_config
    config = (get_api_key(), get_base_url(model))
    if _sync_client is None or _sync_client_config != config:
        _sync_client = OpenAI(api_key=config[0], base_url=config[1])
        _sync_client_config = config
    return _sync_client


def _get_async_openai_client(model: str | None = None) -> AsyncOpenAI:
    global _async_client, _async_client_config
    config = (get_api_key(), get_base_url(model))
    if _async_client is None or _async_client_config != config:
        _async_client = AsyncOpenAI(api_key=config[0], base_url=config[1])
        _async_client_config = config
    return _async_client


def configure_runtime(model: str | None = None) -> None:
    """Configure the agents SDK for the active provider."""
    global _agents_configured

    if not uses_responses_api(model):
        return

    config = (get_api_key(), get_base_url(model), get_wire_api(model))
    if _agents_configured == config:
        return

    set_default_openai_client(_get_async_openai_client(model), use_for_tracing=False)
    set_default_openai_api("responses")
    _agents_configured = config


def resolve_agent_model(model: str) -> str:
    return model if uses_responses_api(model) else f"litellm/{model}"


def build_agent_model_settings(*, parallel_tool_calls: bool | None = None, model: str | None = None) -> ModelSettings:
    settings = ModelSettings(parallel_tool_calls=parallel_tool_calls)
    if uses_responses_api(model):
        settings.store = False if disable_response_storage() else None
        effort = get_reasoning_effort()
        if effort:
            settings.reasoning = Reasoning(effort=effort)
        verbosity = get_verbosity()
        if verbosity in {"low", "medium", "high"}:
            settings.verbosity = verbosity
    return settings


def _split_messages_for_responses(messages: list[dict]) -> tuple[str | None, list[dict]]:
    if messages and messages[0].get("role") == "system":
        instructions = messages[0].get("content", "")
        return instructions, messages[1:]
    return None, messages


def _responses_request_kwargs(model: str, messages: list[dict], **kwargs) -> dict[str, Any]:
    instructions, input_items = _split_messages_for_responses(messages)
    max_tokens = kwargs.pop("max_tokens", None)
    return {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "max_output_tokens": max_tokens,
        "store": False if disable_response_storage() else None,
        "reasoning": Reasoning(effort=get_reasoning_effort()) if get_reasoning_effort() else None,
        "text": {"verbosity": get_verbosity()} if get_verbosity() in {"low", "medium", "high"} else None,
        **kwargs,
    }


def completion(model: str, messages: list[dict], **kwargs) -> CompletionResult:
    if not uses_responses_api(model):
        response = litellm.completion(model=model, messages=messages, **kwargs)
        text = response.choices[0].message.content or ""
        return CompletionResult(text=text.strip(), usage=response.usage)

    client = _get_sync_openai_client(model)
    request_kwargs = _responses_request_kwargs(model, messages, **kwargs)
    max_retries = get_response_max_retries()

    for attempt in range(max_retries + 1):
        try:
            response = client.responses.create(**request_kwargs)
            return CompletionResult(text=(response.output_text or "").strip(), usage=response.usage)
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable_responses_error(exc):
                raise
            time.sleep(_retry_delay_seconds(attempt, exc))


async def acompletion(model: str, messages: list[dict], **kwargs) -> CompletionResult:
    if not uses_responses_api(model):
        response = await litellm.acompletion(model=model, messages=messages, **kwargs)
        text = response.choices[0].message.content or ""
        return CompletionResult(text=text.strip(), usage=response.usage)

    client = _get_async_openai_client(model)
    request_kwargs = _responses_request_kwargs(model, messages, **kwargs)
    max_retries = get_response_max_retries()

    for attempt in range(max_retries + 1):
        try:
            response = await client.responses.create(**request_kwargs)
            return CompletionResult(text=(response.output_text or "").strip(), usage=response.usage)
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable_responses_error(exc):
                raise
            await asyncio.sleep(_retry_delay_seconds(attempt, exc))
