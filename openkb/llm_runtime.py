from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
import time
from dataclasses import dataclass
from typing import Any

import litellm
import requests
from agents import set_default_openai_api, set_default_openai_client
from agents.model_settings import ModelSettings
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, OpenAI
from openai.types.shared import Reasoning


@dataclass
class CompletionResult:
    text: str
    usage: Any


@dataclass(frozen=True)
class LlmRuntimeConfig:
    api_key: str = ""
    wire_api: str = ""
    base_url: str = ""
    provider: str = ""
    reasoning_effort: str = ""
    thinking_enabled: bool | None = None


_async_client: AsyncOpenAI | None = None
_async_client_config: tuple[str | None, str | None] | None = None
_sync_client: OpenAI | None = None
_sync_client_config: tuple[str | None, str | None] | None = None
_agents_configured: tuple[str | None, str | None, str] | None = None
_LOW_LEVEL_USAGE_LOGGING_SUPPRESSED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "openkb_low_level_usage_logging_suppressed",
    default=False,
)
_ACTIVE_RUNTIME_CONFIG: contextvars.ContextVar[LlmRuntimeConfig | None] = contextvars.ContextVar(
    "openkb_llm_runtime_config",
    default=None,
)
_ORIGINAL_LITELLM_ACOMPLETION = litellm.acompletion


@contextlib.contextmanager
def llm_runtime_context(config: dict[str, Any] | LlmRuntimeConfig | None):
    """Use request/task-local LLM runtime settings without mutating os.environ."""
    if isinstance(config, LlmRuntimeConfig):
        runtime_config = config
    else:
        raw = config or {}
        runtime_config = LlmRuntimeConfig(
            api_key=str(raw.get("api_key") or "").strip(),
            wire_api=str(raw.get("wire_api") or "").strip().lower(),
            base_url=str(raw.get("base_url") or "").strip().rstrip("/"),
            provider=str(raw.get("provider") or "").strip().lower(),
            reasoning_effort=str(raw.get("reasoning_effort") or "").strip().lower(),
            thinking_enabled=(bool(raw.get("thinking_enabled")) if "thinking_enabled" in raw else None),
        )
    token = _ACTIVE_RUNTIME_CONFIG.set(runtime_config)
    try:
        yield runtime_config
    finally:
        _ACTIVE_RUNTIME_CONFIG.reset(token)


def _runtime_config() -> LlmRuntimeConfig | None:
    return _ACTIVE_RUNTIME_CONFIG.get()


def runtime_env_overlay(model: str | None = None) -> dict[str, str]:
    """Return environment variables implied by the active runtime context."""
    overlay: dict[str, str] = {}
    api_key = get_api_key()
    if api_key:
        overlay["LLM_API_KEY"] = api_key
        overlay["OPENAI_API_KEY"] = api_key
    wire_api = get_wire_api(model)
    if wire_api:
        overlay["OPENKB_WIRE_API"] = wire_api
    base_url = get_base_url(model)
    if base_url:
        overlay["OPENAI_BASE_URL"] = base_url
        overlay["OPENAI_API_BASE"] = base_url
    provider = get_model_provider()
    if provider:
        overlay["OPENKB_MODEL_PROVIDER"] = provider
    effort = get_reasoning_effort()
    if effort:
        overlay["OPENKB_MODEL_REASONING_EFFORT"] = effort
    if deepseek_thinking_enabled():
        overlay["OPENKB_DEEPSEEK_THINKING_ENABLED"] = "true"
    return overlay


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump())
        except Exception:
            return str(value)
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


def _usage_payload(usage: Any) -> Any:
    return _json_safe(usage)


def _current_usage_context() -> Any:
    try:
        from openkb.llm_usage import get_llm_usage_context
    except ModuleNotFoundError:
        return None
    return get_llm_usage_context()


@contextlib.contextmanager
def _suppress_low_level_usage_logging():
    token = _LOW_LEVEL_USAGE_LOGGING_SUPPRESSED.set(True)
    try:
        yield
    finally:
        _LOW_LEVEL_USAGE_LOGGING_SUPPRESSED.reset(token)


def _should_log_low_level_usage() -> bool:
    return not _LOW_LEVEL_USAGE_LOGGING_SUPPRESSED.get() and _current_usage_context() is not None


def _record_usage_from_context(
    *,
    model: str,
    wire_api: str,
    base_url: str,
    status: str,
    duration_ms: int,
    error: str = "",
    input_payload: Any = None,
    output_payload: Any = None,
) -> None:
    context = _current_usage_context()
    if context is None:
        return
    from openkb.llm_usage import record_usage

    record_usage(
        kb_dir=context.kb_dir,
        feature=context.feature,
        model=model,
        wire_api=wire_api,
        base_url=base_url,
        status=status,
        duration_ms=duration_ms,
        error=error,
        input_payload=input_payload,
        output_payload=output_payload,
    )


def _elapsed_ms(started_at: float) -> int:
    return max(int((time.perf_counter() - started_at) * 1000), 0)


def _request_payload_for_completion(model: str, messages: list[dict], kwargs: dict[str, Any]) -> dict[str, Any]:
    payload_kwargs = dict(kwargs)
    if uses_responses_api(model):
        return _json_safe(_responses_request_kwargs(model, list(messages), **dict(payload_kwargs)))
    normalized_model = normalize_model_name(model)
    if is_custom_openai_compatible(normalized_model):
        return _json_safe(_custom_openai_payload(normalized_model, list(messages), **dict(payload_kwargs)))
    return _json_safe(
        {
            "model": normalized_model,
            "messages": list(messages),
            "kwargs": _apply_default_timeout(payload_kwargs),
        }
    )


def _request_payload_for_litellm_call(*args, **kwargs) -> dict[str, Any]:
    model = kwargs.get("model")
    messages = kwargs.get("messages")
    if model is None and args:
        model = args[0]
    if messages is None and len(args) > 1:
        messages = args[1]
    return _json_safe(
        {
            "model": model,
            "messages": messages,
            "kwargs": dict(kwargs),
        }
    )


def _text_from_litellm_response(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    return ((getattr(message, "content", "") or "") if message is not None else "").strip()


def _response_payload_from_completion_result(result: CompletionResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "usage": _usage_payload(result.usage),
    }


def _response_payload_from_litellm_response(response: Any) -> dict[str, Any]:
    return {
        "text": _text_from_litellm_response(response),
        "usage": _usage_payload(getattr(response, "usage", None)),
    }


def _response_payload_from_openai_response(response: Any, fallback_text: str = "") -> dict[str, Any]:
    text = str(getattr(response, "output_text", "") or fallback_text).strip()
    return {
        "text": text,
        "usage": _usage_payload(getattr(response, "usage", None)),
    }


class _UsageLoggingAsyncStream:
    def __init__(
        self,
        stream: Any,
        *,
        model: str,
        wire_api: str,
        base_url: str,
        input_payload: Any,
        started_at: float,
    ) -> None:
        self._stream = stream
        self._model = model
        self._wire_api = wire_api
        self._base_url = base_url
        self._input_payload = input_payload
        self._started_at = started_at
        self._collected: list[str] = []
        self._logged = False

    def __aiter__(self) -> "_UsageLoggingAsyncStream":
        return self

    async def __anext__(self) -> Any:
        try:
            event = await self._stream.__anext__()
        except StopAsyncIteration:
            self._log_success({"text": "".join(self._collected)})
            raise
        except Exception as exc:
            self._log_failure(exc, {"text": "".join(self._collected)})
            raise
        self._consume_event(event)
        return event

    async def aclose(self) -> None:
        aclose = getattr(self._stream, "aclose", None)
        if callable(aclose):
            await aclose()

    def _consume_event(self, event: Any) -> None:
        event_type = getattr(event, "type", "")
        if event_type == "response.output_text.delta":
            delta = getattr(event, "delta", "") or ""
            if delta:
                self._collected.append(str(delta))
            return
        if event_type == "response.completed":
            response = getattr(event, "response", None)
            self._log_success(_response_payload_from_openai_response(response, "".join(self._collected)))
            return
        if event_type in {"response.failed", "response.incomplete", "response.error"}:
            response = getattr(event, "response", None)
            error_value = getattr(event, "error", None) or getattr(response, "error", None) or str(event)
            self._log_failure(error_value, _response_payload_from_openai_response(response, "".join(self._collected)))

    def _log_success(self, output_payload: Any) -> None:
        if self._logged:
            return
        self._logged = True
        _record_usage_from_context(
            model=self._model,
            wire_api=self._wire_api,
            base_url=self._base_url,
            status="succeeded",
            duration_ms=_elapsed_ms(self._started_at),
            input_payload=self._input_payload,
            output_payload=output_payload,
        )

    def _log_failure(self, exc: Any, output_payload: Any) -> None:
        if self._logged:
            return
        self._logged = True
        _record_usage_from_context(
            model=self._model,
            wire_api=self._wire_api,
            base_url=self._base_url,
            status="failed",
            duration_ms=_elapsed_ms(self._started_at),
            error=str(exc),
            input_payload=self._input_payload,
            output_payload=output_payload,
        )


class _StreamingApiResponseProxy:
    def __init__(
        self,
        inner: Any,
        *,
        model: str,
        base_url: str,
        input_payload: Any,
        started_at: float,
    ) -> None:
        self._inner = inner
        self._model = model
        self._base_url = base_url
        self._input_payload = input_payload
        self._started_at = started_at

    async def parse(self) -> Any:
        parsed = await self._inner.parse()
        if not _should_log_low_level_usage():
            return parsed
        return _UsageLoggingAsyncStream(
            parsed,
            model=self._model,
            wire_api="responses",
            base_url=self._base_url,
            input_payload=self._input_payload,
            started_at=self._started_at,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _StreamingCreateContextManagerProxy:
    def __init__(
        self,
        inner: Any,
        *,
        model: str,
        base_url: str,
        input_payload: Any,
    ) -> None:
        self._inner = inner
        self._model = model
        self._base_url = base_url
        self._input_payload = input_payload
        self._started_at = time.perf_counter()

    async def __aenter__(self) -> Any:
        entered = await self._inner.__aenter__()
        if not _should_log_low_level_usage():
            return entered
        return _StreamingApiResponseProxy(
            entered,
            model=self._model,
            base_url=self._base_url,
            input_payload=self._input_payload,
            started_at=self._started_at,
        )

    async def __aexit__(self, exc_type, exc, tb) -> Any:
        return await self._inner.__aexit__(exc_type, exc, tb)


class _StreamingResponsesProxy:
    def __init__(self, inner: Any, *, model: str) -> None:
        self._inner = inner
        self._model = model

    def create(self, **kwargs) -> Any:
        if not _should_log_low_level_usage():
            return self._inner.create(**kwargs)
        model = str(kwargs.get("model") or self._model or "")
        return _StreamingCreateContextManagerProxy(
            self._inner.create(**kwargs),
            model=model,
            base_url=get_base_url(model) or "",
            input_payload=_json_safe(kwargs),
        )


class _ResponsesProxy:
    def __init__(self, inner: Any, *, model: str) -> None:
        self._inner = inner
        self._model = model

    async def create(self, **kwargs) -> Any:
        if not _should_log_low_level_usage():
            return await self._inner.create(**kwargs)
        started_at = time.perf_counter()
        model = str(kwargs.get("model") or self._model or "")
        input_payload = _json_safe(kwargs)
        try:
            response = await self._inner.create(**kwargs)
        except Exception as exc:
            _record_usage_from_context(
                model=model,
                wire_api="responses",
                base_url=get_base_url(model) or "",
                status="failed",
                duration_ms=_elapsed_ms(started_at),
                error=str(exc),
                input_payload=input_payload,
                output_payload={"text": ""},
            )
            raise
        _record_usage_from_context(
            model=model,
            wire_api="responses",
            base_url=get_base_url(model) or "",
            status="succeeded",
            duration_ms=_elapsed_ms(started_at),
            input_payload=input_payload,
            output_payload=_response_payload_from_openai_response(response),
        )
        return response

    @property
    def with_streaming_response(self) -> Any:
        streaming = getattr(self._inner, "with_streaming_response", None)
        if streaming is None:
            return None
        return _StreamingResponsesProxy(streaming, model=self._model)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _AsyncOpenAIClientProxy:
    def __init__(self, inner: AsyncOpenAI, *, model: str | None) -> None:
        self._inner = inner
        self._model = model

    @property
    def responses(self) -> Any:
        return _ResponsesProxy(self._inner.responses, model=self._model or "")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


async def _instrumented_litellm_acompletion(*args, **kwargs) -> Any:
    if not _should_log_low_level_usage():
        return await _ORIGINAL_LITELLM_ACOMPLETION(*args, **kwargs)
    started_at = time.perf_counter()
    request_payload = _request_payload_for_litellm_call(*args, **kwargs)
    model = str(kwargs.get("model") or (args[0] if args else ""))
    base_url = str(kwargs.get("base_url") or get_base_url(model) or "")
    try:
        response = await _ORIGINAL_LITELLM_ACOMPLETION(*args, **kwargs)
    except Exception as exc:
        _record_usage_from_context(
            model=model,
            wire_api="chat_completions",
            base_url=base_url,
            status="failed",
            duration_ms=_elapsed_ms(started_at),
            error=str(exc),
            input_payload=request_payload,
            output_payload={"text": ""},
        )
        raise
    _record_usage_from_context(
        model=model,
        wire_api="chat_completions",
        base_url=base_url,
        status="succeeded",
        duration_ms=_elapsed_ms(started_at),
        input_payload=request_payload,
        output_payload=_response_payload_from_litellm_response(response),
    )
    return response


def _install_low_level_usage_wrappers() -> None:
    if getattr(litellm.acompletion, "__openkb_usage_wrapped__", False):
        return
    _instrumented_litellm_acompletion.__openkb_usage_wrapped__ = True
    litellm.acompletion = _instrumented_litellm_acompletion


def _extract_reasoning_content_text(item: Any) -> str:
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return ""
    parts: list[str] = []
    for summary in item.get("summary") or []:
        if isinstance(summary, dict) and summary.get("text"):
            parts.append(str(summary["text"]))
    for content in item.get("content") or []:
        if (
            isinstance(content, dict)
            and content.get("type") == "reasoning_text"
            and content.get("text")
        ):
            parts.append(str(content["text"]))
    return "\n".join(part for part in parts if part)


def _reasoning_content_markers(items: list[Any]) -> list[str | None]:
    """Map response-input history items to assistant-message reasoning payloads.

    Some OpenAI-compatible thinking endpoints expose DeepSeek-style
    ``reasoning_content`` through generic gateways. The Agents SDK only replays
    that field when the model name/provider looks like DeepSeek and the message
    has tool calls, so OpenKB preserves it based on the actual history item
    shape instead of the configured profile type.
    """
    markers: list[str | None] = []
    pending_reasoning: str | None = None
    current_assistant_index: int | None = None

    def flush_assistant() -> None:
        nonlocal current_assistant_index
        current_assistant_index = None

    for item in items:
        if not isinstance(item, dict):
            continue

        reasoning = _extract_reasoning_content_text(item)
        if reasoning:
            pending_reasoning = reasoning
            continue

        item_type = str(item.get("type") or "")
        role = str(item.get("role") or "")

        if role in {"user", "system", "developer"} or item_type == "function_call_output":
            flush_assistant()
            continue

        if role == "assistant" or (item_type == "message" and role == "assistant"):
            flush_assistant()
            markers.append(pending_reasoning)
            pending_reasoning = None
            current_assistant_index = len(markers) - 1
            continue

        if item_type in {"function_call", "file_search_call", "computer_call"}:
            if current_assistant_index is None:
                markers.append(pending_reasoning)
                current_assistant_index = len(markers) - 1
            elif pending_reasoning and markers[current_assistant_index] is None:
                markers[current_assistant_index] = pending_reasoning
            pending_reasoning = None

    return markers


def _install_reasoning_content_history_patch() -> None:
    try:
        from agents.models.chatcmpl_converter import Converter
    except Exception:
        return

    if getattr(Converter.items_to_messages, "__openkb_reasoning_content_patched__", False):
        return

    original = Converter.items_to_messages

    def patched_items_to_messages(
        cls,
        items,
        model: str | None = None,
        preserve_thinking_blocks: bool = False,
        preserve_tool_output_all_content: bool = False,
    ):
        item_list = None if isinstance(items, str) else list(items)
        messages = original(
            items if item_list is None else item_list,
            model=model,
            preserve_thinking_blocks=preserve_thinking_blocks,
            preserve_tool_output_all_content=preserve_tool_output_all_content,
        )
        if item_list is None:
            return messages

        markers = _reasoning_content_markers(item_list)
        if not any(markers):
            return messages
        assistant_index = 0
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            reasoning = markers[assistant_index] if assistant_index < len(markers) else None
            if reasoning and not message.get("reasoning_content"):
                message["reasoning_content"] = reasoning
            assistant_index += 1
        return messages

    patched_items_to_messages.__openkb_reasoning_content_patched__ = True
    Converter.items_to_messages = classmethod(patched_items_to_messages)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "")
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _configured_wire_api() -> str:
    runtime = _runtime_config()
    if runtime is not None and runtime.wire_api:
        return runtime.wire_api
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
    if configured == "auto":
        return "responses" if model_prefers_responses_api(model) else "chat_completions"
    return configured


def uses_responses_api(model: str | None = None) -> bool:
    return get_wire_api(model) == "responses"


def get_api_key() -> str | None:
    runtime = _runtime_config()
    if runtime is not None and runtime.api_key:
        return runtime.api_key
    return os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")


def get_base_url(model: str | None = None) -> str | None:
    runtime = _runtime_config()
    if runtime is not None and runtime.base_url:
        base_url = runtime.base_url
        if uses_responses_api(model) and not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        return base_url
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
    if not base_url:
        return None
    base_url = base_url.rstrip("/")
    if uses_responses_api(model) and not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return base_url


def normalize_model_name(model: str) -> str:
    normalized = model.strip()
    if not normalized or uses_responses_api(normalized):
        return normalized
    if normalized.startswith("openai/") and get_base_url(normalized):
        return normalized.split("/", 1)[1]
    if "/" in normalized:
        return normalized
    return normalized


def is_custom_openai_compatible(model: str | None = None) -> bool:
    return bool(get_base_url(model)) and not uses_responses_api(model)


def get_model_provider() -> str | None:
    runtime = _runtime_config()
    if runtime is not None and runtime.provider:
        return runtime.provider
    value = os.getenv("OPENKB_MODEL_PROVIDER", "").strip().lower()
    return value or None


def get_reasoning_effort() -> str | None:
    runtime = _runtime_config()
    if runtime is not None and runtime.reasoning_effort:
        return runtime.reasoning_effort
    value = os.getenv("OPENKB_MODEL_REASONING_EFFORT", "").strip().lower()
    return value or None


def deepseek_thinking_enabled() -> bool:
    runtime = _runtime_config()
    if runtime is not None and runtime.thinking_enabled is not None:
        return runtime.thinking_enabled
    return _env_flag("OPENKB_DEEPSEEK_THINKING_ENABLED", default=False)


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


def get_llm_max_retries() -> int:
    value = os.getenv("OPENKB_LLM_MAX_RETRIES", os.getenv("OPENKB_RESPONSE_MAX_RETRIES", "")).strip()
    if not value:
        return 2
    try:
        return max(int(value), 0)
    except ValueError:
        return 2


def get_llm_retry_max_delay() -> float:
    value = os.getenv("OPENKB_LLM_RETRY_MAX_DELAY", os.getenv("OPENKB_RESPONSE_RETRY_MAX_DELAY", "")).strip()
    if not value:
        return 10.0
    try:
        return max(float(value), 0.0)
    except ValueError:
        return 10.0


def get_llm_timeout() -> float | None:
    value = os.getenv("OPENKB_LLM_TIMEOUT", "").strip().lower()
    if value in {"0", "none", "false", "off"}:
        return None
    if not value:
        return 180.0
    try:
        timeout = float(value)
    except ValueError:
        return 180.0
    return timeout if timeout > 0 else None


def _apply_default_timeout(kwargs: dict[str, Any]) -> dict[str, Any]:
    if "timeout" not in kwargs:
        timeout = get_llm_timeout()
        if timeout is not None:
            kwargs["timeout"] = timeout
    return kwargs


def _custom_openai_payload(model: str, messages: list[dict], **kwargs) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": normalize_model_name(model),
        "messages": messages,
        "stream": False,
    }
    for key in (
        "max_tokens",
        "temperature",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "response_format",
        "stop",
        "seed",
        "user",
        "tools",
        "tool_choice",
    ):
        value = kwargs.get(key)
        if value is not None:
            payload[key] = value
    if get_model_provider() == "deepseek":
        effort = get_reasoning_effort()
        if effort:
            payload["reasoning_effort"] = effort
        if deepseek_thinking_enabled():
            payload["thinking"] = {"type": "enabled"}
    return payload


def _custom_openai_response_json(response: requests.Response) -> Any:
    raw = getattr(response, "content", b"") or b""
    if isinstance(raw, bytes) and raw:
        try:
            return requests.models.complexjson.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            pass
    return response.json()


def _custom_openai_completion_via_requests(model: str, messages: list[dict], **kwargs) -> CompletionResult:
    timeout = _apply_default_timeout(kwargs).get("timeout")
    api_key = get_api_key()
    base_url = get_base_url(model)
    if not api_key:
        raise ValueError("Missing API key for custom OpenAI-compatible endpoint.")
    if not base_url:
        raise ValueError("Missing base URL for custom OpenAI-compatible endpoint.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers=headers,
        json=_custom_openai_payload(model, messages, **kwargs),
        stream=False,
        timeout=timeout,
    )
    response.raise_for_status()
    data = _custom_openai_response_json(response)
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError(
            "Custom OpenAI-compatible endpoint did not return any choices. "
            f"Response keys: {sorted(data.keys()) if isinstance(data, dict) else type(data).__name__}"
        )
    first_choice = choices[0] if isinstance(choices[0], dict) else None
    message = first_choice.get("message") if isinstance(first_choice, dict) else None
    if not isinstance(message, dict):
        raise ValueError(
            "Custom OpenAI-compatible endpoint returned a choice without a message payload."
        )
    text = message.get("content") or ""
    return CompletionResult(text=text.strip(), usage=data.get("usage"))


def _litellm_runtime_kwargs(model: str) -> dict[str, Any]:
    runtime_kwargs: dict[str, Any] = {}
    api_key = get_api_key()
    base_url = get_base_url(model)
    if api_key:
        runtime_kwargs["api_key"] = api_key
    if base_url:
        runtime_kwargs["base_url"] = base_url
    return runtime_kwargs


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


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
    return _is_retryable_llm_error(exc)


def _is_retryable_llm_error(exc: Exception) -> bool:
    if isinstance(exc, (
        APIConnectionError,
        APITimeoutError,
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    )):
        return True

    body = _error_body(exc)
    if body.get("retryable") is True:
        return True

    status = _status_code(exc)
    return status in {408, 409, 429} or (status is not None and status >= 500)


def _retry_delay_seconds(attempt: int, exc: Exception) -> float:
    max_delay = get_llm_retry_max_delay()
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, max_delay)
    return min(float(2 ** attempt), max_delay)


def _get_sync_openai_client(model: str | None = None) -> OpenAI:
    global _sync_client, _sync_client_config
    config = (get_api_key(), get_base_url(model))
    if _runtime_config() is not None:
        return OpenAI(api_key=config[0], base_url=config[1])
    if _sync_client is None or _sync_client_config != config:
        _sync_client = OpenAI(api_key=config[0], base_url=config[1])
        _sync_client_config = config
    return _sync_client


def _get_async_openai_client(model: str | None = None) -> Any:
    global _async_client, _async_client_config
    config = (get_api_key(), get_base_url(model))
    if _runtime_config() is not None:
        return _AsyncOpenAIClientProxy(AsyncOpenAI(api_key=config[0], base_url=config[1]), model=model)
    if _async_client is None or _async_client_config != config:
        _async_client = AsyncOpenAI(api_key=config[0], base_url=config[1])
        _async_client_config = config
    return _AsyncOpenAIClientProxy(_async_client, model=model)


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
    normalized = normalize_model_name(model)
    if uses_responses_api(normalized):
        return normalized
    if is_custom_openai_compatible(normalized):
        return f"litellm/custom_openai/{normalized}"
    return f"litellm/{normalized}"


def build_agent_model_settings(*, parallel_tool_calls: bool | None = None, model: str | None = None) -> ModelSettings:
    settings = ModelSettings(parallel_tool_calls=parallel_tool_calls)
    provider = get_model_provider()
    if uses_responses_api(model):
        settings.store = False if disable_response_storage() else None
        effort = get_reasoning_effort()
        if effort:
            settings.reasoning = Reasoning(effort=effort)
        verbosity = get_verbosity()
        if verbosity in {"low", "medium", "high"}:
            settings.verbosity = verbosity
    elif provider == "deepseek":
        extra_body: dict[str, Any] = {}
        effort = get_reasoning_effort()
        if effort:
            extra_body["reasoning_effort"] = effort
        if deepseek_thinking_enabled():
            extra_body["thinking"] = {"type": "enabled"}
        if extra_body:
            settings.extra_body = extra_body
    return settings


def _split_messages_for_responses(messages: list[dict]) -> tuple[str | None, list[dict]]:
    if messages and messages[0].get("role") == "system":
        instructions = messages[0].get("content", "")
        return instructions, messages[1:]
    return None, messages


def _responses_request_kwargs(model: str, messages: list[dict], **kwargs) -> dict[str, Any]:
    instructions, input_items = _split_messages_for_responses(messages)
    max_tokens = kwargs.pop("max_tokens", None)
    return _apply_default_timeout({
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "max_output_tokens": max_tokens,
        "store": False if disable_response_storage() else None,
        "reasoning": Reasoning(effort=get_reasoning_effort()) if get_reasoning_effort() else None,
        "text": {"verbosity": get_verbosity()} if get_verbosity() in {"low", "medium", "high"} else None,
        **kwargs,
    })


def _completion_once(model: str, messages: list[dict], **kwargs) -> CompletionResult:
    if not uses_responses_api(model):
        normalized_model = normalize_model_name(model)
        if is_custom_openai_compatible(normalized_model):
            return _custom_openai_completion_via_requests(normalized_model, messages, **kwargs)
        response = litellm.completion(
            model=normalized_model,
            messages=messages,
            **_litellm_runtime_kwargs(normalized_model),
            **_apply_default_timeout(kwargs),
        )
        text = response.choices[0].message.content or ""
        return CompletionResult(text=text.strip(), usage=response.usage)

    client = _get_sync_openai_client(model)
    request_kwargs = _responses_request_kwargs(model, messages, **kwargs)
    response = client.responses.create(**request_kwargs)
    return CompletionResult(text=(response.output_text or "").strip(), usage=response.usage)


def completion(model: str, messages: list[dict], **kwargs) -> CompletionResult:
    max_retries = get_llm_max_retries()
    request_payload = _request_payload_for_completion(model, messages, kwargs)
    wire_api = get_wire_api(model)
    effective_model = (
        normalize_model_name(model)
        if not uses_responses_api(model)
        else model.strip()
    )
    base_url = get_base_url(model) or ""
    started_at = time.perf_counter()

    for attempt in range(max_retries + 1):
        try:
            with _suppress_low_level_usage_logging():
                result = _completion_once(model, messages, **kwargs)
            _record_usage_from_context(
                model=effective_model,
                wire_api=wire_api,
                base_url=base_url,
                status="succeeded",
                duration_ms=_elapsed_ms(started_at),
                input_payload=request_payload,
                output_payload=_response_payload_from_completion_result(result),
            )
            return result
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable_llm_error(exc):
                _record_usage_from_context(
                    model=effective_model,
                    wire_api=wire_api,
                    base_url=base_url,
                    status="failed",
                    duration_ms=_elapsed_ms(started_at),
                    error=str(exc),
                    input_payload=request_payload,
                    output_payload={"text": ""},
                )
                raise
            time.sleep(_retry_delay_seconds(attempt, exc))
    raise RuntimeError("LLM completion retry loop exhausted.")


async def _acompletion_once(model: str, messages: list[dict], **kwargs) -> CompletionResult:
    if not uses_responses_api(model):
        normalized_model = normalize_model_name(model)
        if is_custom_openai_compatible(normalized_model):
            return await asyncio.to_thread(_custom_openai_completion_via_requests, normalized_model, messages, **kwargs)
        response = await litellm.acompletion(
            model=normalized_model,
            messages=messages,
            **_litellm_runtime_kwargs(normalized_model),
            **_apply_default_timeout(kwargs),
        )
        text = response.choices[0].message.content or ""
        return CompletionResult(text=text.strip(), usage=response.usage)

    client = _get_async_openai_client(model)
    request_kwargs = _responses_request_kwargs(model, messages, **kwargs)
    response = await client.responses.create(**request_kwargs)
    return CompletionResult(text=(response.output_text or "").strip(), usage=response.usage)


async def acompletion(model: str, messages: list[dict], **kwargs) -> CompletionResult:
    max_retries = get_llm_max_retries()
    request_payload = _request_payload_for_completion(model, messages, kwargs)
    wire_api = get_wire_api(model)
    effective_model = (
        normalize_model_name(model)
        if not uses_responses_api(model)
        else model.strip()
    )
    base_url = get_base_url(model) or ""
    started_at = time.perf_counter()

    for attempt in range(max_retries + 1):
        try:
            with _suppress_low_level_usage_logging():
                result = await _acompletion_once(model, messages, **kwargs)
            _record_usage_from_context(
                model=effective_model,
                wire_api=wire_api,
                base_url=base_url,
                status="succeeded",
                duration_ms=_elapsed_ms(started_at),
                input_payload=request_payload,
                output_payload=_response_payload_from_completion_result(result),
            )
            return result
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable_llm_error(exc):
                _record_usage_from_context(
                    model=effective_model,
                    wire_api=wire_api,
                    base_url=base_url,
                    status="failed",
                    duration_ms=_elapsed_ms(started_at),
                    error=str(exc),
                    input_payload=request_payload,
                    output_payload={"text": ""},
                )
                raise
            await asyncio.sleep(_retry_delay_seconds(attempt, exc))
    raise RuntimeError("LLM completion retry loop exhausted.")


_install_low_level_usage_wrappers()
_install_reasoning_content_history_patch()
