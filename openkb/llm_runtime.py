from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import litellm
from agents import set_default_openai_api, set_default_openai_client
from agents.model_settings import ModelSettings
from openai import AsyncOpenAI, OpenAI
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


def get_wire_api() -> str:
    return os.getenv("OPENKB_WIRE_API", os.getenv("OPENAI_WIRE_API", "chat_completions")).strip().lower()


def uses_responses_api() -> bool:
    return get_wire_api() == "responses"


def get_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")


def get_base_url() -> str | None:
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
    if not base_url:
        return None
    base_url = base_url.rstrip("/")
    if uses_responses_api() and not base_url.endswith("/v1"):
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


def _get_sync_openai_client() -> OpenAI:
    global _sync_client, _sync_client_config
    config = (get_api_key(), get_base_url())
    if _sync_client is None or _sync_client_config != config:
        _sync_client = OpenAI(api_key=config[0], base_url=config[1])
        _sync_client_config = config
    return _sync_client


def _get_async_openai_client() -> AsyncOpenAI:
    global _async_client, _async_client_config
    config = (get_api_key(), get_base_url())
    if _async_client is None or _async_client_config != config:
        _async_client = AsyncOpenAI(api_key=config[0], base_url=config[1])
        _async_client_config = config
    return _async_client


def configure_runtime() -> None:
    """Configure the agents SDK for the active provider."""
    global _agents_configured

    if not uses_responses_api():
        return

    config = (get_api_key(), get_base_url(), get_wire_api())
    if _agents_configured == config:
        return

    set_default_openai_client(_get_async_openai_client(), use_for_tracing=False)
    set_default_openai_api("responses")
    _agents_configured = config


def resolve_agent_model(model: str) -> str:
    return model if uses_responses_api() else f"litellm/{model}"


def build_agent_model_settings(*, parallel_tool_calls: bool | None = None) -> ModelSettings:
    settings = ModelSettings(parallel_tool_calls=parallel_tool_calls)
    if uses_responses_api():
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


def completion(model: str, messages: list[dict], **kwargs) -> CompletionResult:
    if not uses_responses_api():
        response = litellm.completion(model=model, messages=messages, **kwargs)
        text = response.choices[0].message.content or ""
        return CompletionResult(text=text.strip(), usage=response.usage)

    client = _get_sync_openai_client()
    instructions, input_items = _split_messages_for_responses(messages)
    max_tokens = kwargs.pop("max_tokens", None)

    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=input_items,
        max_output_tokens=max_tokens,
        store=False if disable_response_storage() else None,
        reasoning=Reasoning(effort=get_reasoning_effort()) if get_reasoning_effort() else None,
        text={"verbosity": get_verbosity()} if get_verbosity() in {"low", "medium", "high"} else None,
        **kwargs,
    )
    return CompletionResult(text=(response.output_text or "").strip(), usage=response.usage)


async def acompletion(model: str, messages: list[dict], **kwargs) -> CompletionResult:
    if not uses_responses_api():
        response = await litellm.acompletion(model=model, messages=messages, **kwargs)
        text = response.choices[0].message.content or ""
        return CompletionResult(text=text.strip(), usage=response.usage)

    client = _get_async_openai_client()
    instructions, input_items = _split_messages_for_responses(messages)
    max_tokens = kwargs.pop("max_tokens", None)

    response = await client.responses.create(
        model=model,
        instructions=instructions,
        input=input_items,
        max_output_tokens=max_tokens,
        store=False if disable_response_storage() else None,
        reasoning=Reasoning(effort=get_reasoning_effort()) if get_reasoning_effort() else None,
        text={"verbosity": get_verbosity()} if get_verbosity() in {"low", "medium", "high"} else None,
        **kwargs,
    )
    return CompletionResult(text=(response.output_text or "").strip(), usage=response.usage)
