from __future__ import annotations

from importlib import import_module
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from openkb.llm_runtime import acompletion, completion, resolve_agent_model, uses_responses_api


class _RetryableError(Exception):
    def __init__(self, status_code: int = 502, body: dict | None = None):
        super().__init__(f"status={status_code}")
        self.status_code = status_code
        self.body = body or {"retryable": True, "retry_after": 0}


class _BadRequestError(Exception):
    def __init__(self, status_code: int = 400, body: dict | None = None):
        super().__init__(f"status={status_code}")
        self.status_code = status_code
        self.body = body or {"retryable": False}


def _fake_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(output_text=text, usage=SimpleNamespace())


def _fake_chat_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text),
            )
        ],
        usage=SimpleNamespace(),
    )


def _fake_requests_response(text: str) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": text,
                }
            }
        ],
        "usage": {},
    }
    response.raise_for_status.return_value = None
    return response


def _clear_gateway_env(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)


def _make_usage_kb(tmp_path) -> object:
    kb_dir = tmp_path / "kb"
    (kb_dir / ".openkb").mkdir(parents=True)
    return kb_dir


def test_completion_retries_retryable_responses_error(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "responses")

    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [
        _RetryableError(),
        _fake_response("OK"),
    ]

    with patch("openkb.llm_runtime._get_sync_openai_client", return_value=mock_client), \
         patch("openkb.llm_runtime.time.sleep") as mock_sleep:
        result = completion(
            model="gpt-5.4",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    assert mock_client.responses.create.call_count == 2
    mock_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_acompletion_retries_retryable_responses_error(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "responses")

    mock_client = MagicMock()
    mock_client.responses.create = AsyncMock(side_effect=[
        _RetryableError(),
        _fake_response("OK"),
    ])

    with patch("openkb.llm_runtime._get_async_openai_client", return_value=mock_client), \
         patch("openkb.llm_runtime.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await acompletion(
            model="gpt-5.4",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    assert mock_client.responses.create.await_count == 2
    mock_sleep.assert_awaited_once()


def test_completion_does_not_retry_bad_request(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "responses")

    mock_client = MagicMock()
    mock_client.responses.create.side_effect = _BadRequestError()

    with patch("openkb.llm_runtime._get_sync_openai_client", return_value=mock_client), \
         patch("openkb.llm_runtime.time.sleep") as mock_sleep:
        with pytest.raises(_BadRequestError):
            completion(
                model="gpt-5.4",
                messages=[{"role": "user", "content": "Reply with OK"}],
            )

    assert mock_client.responses.create.call_count == 1
    mock_sleep.assert_not_called()


def test_completion_retries_retryable_chat_completion_error(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    _clear_gateway_env(monkeypatch)

    mock_chat_completion = MagicMock(side_effect=[
        _RetryableError(status_code=429),
        _fake_chat_response("OK"),
    ])

    with patch("openkb.llm_runtime.litellm.completion", mock_chat_completion), \
         patch("openkb.llm_runtime.time.sleep") as mock_sleep:
        result = completion(
            model="gpt-5.4",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    assert mock_chat_completion.call_count == 2
    mock_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_acompletion_retries_retryable_chat_completion_error(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    _clear_gateway_env(monkeypatch)

    mock_chat_completion = AsyncMock(side_effect=[
        _RetryableError(status_code=429),
        _fake_chat_response("OK"),
    ])

    with patch("openkb.llm_runtime.litellm.acompletion", mock_chat_completion), \
         patch("openkb.llm_runtime.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await acompletion(
            model="gpt-5.4",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    assert mock_chat_completion.await_count == 2
    mock_sleep.assert_awaited_once()


def test_gpt5_respects_explicit_chat_completions_configuration(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    _clear_gateway_env(monkeypatch)

    assert not uses_responses_api("gpt-5.4")
    assert resolve_agent_model("gpt-5.4") == "litellm/gpt-5.4"
    assert not uses_responses_api("anthropic/claude-sonnet-4-6")


def test_completion_routes_gpt5_to_chat_when_chat_completions_is_configured(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    _clear_gateway_env(monkeypatch)

    mock_client = MagicMock()
    mock_chat_completion = MagicMock(return_value=_fake_chat_response("OK"))

    with patch("openkb.llm_runtime._get_sync_openai_client", return_value=mock_client), \
         patch("openkb.llm_runtime.litellm.completion", mock_chat_completion):
        result = completion(
            model="gpt-5.4",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    mock_client.responses.create.assert_not_called()
    mock_chat_completion.assert_called_once()


def test_completion_passes_configured_timeout_to_responses_api(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "responses")
    monkeypatch.setenv("OPENKB_LLM_TIMEOUT", "12.5")

    mock_client = MagicMock()
    mock_client.responses.create.return_value = _fake_response("OK")

    with patch("openkb.llm_runtime._get_sync_openai_client", return_value=mock_client):
        result = completion(
            model="gpt-5.4",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    assert mock_client.responses.create.call_args.kwargs["timeout"] == 12.5


def test_resolve_agent_model_prefixes_openai_for_custom_gateway_models(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.com/v1")

    assert resolve_agent_model("doubao-seed-2-0-pro-260215") == "litellm/custom_openai/doubao-seed-2-0-pro-260215"


def test_completion_prefixes_openai_for_custom_gateway_models(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    mock_client = MagicMock()
    mock_post = MagicMock(return_value=_fake_requests_response("OK"))

    with patch("openkb.llm_runtime._get_sync_openai_client", return_value=mock_client), \
         patch("openkb.llm_runtime.requests.post", mock_post):
        result = completion(
            model="doubao-seed-2-0-pro-260215",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    mock_client.responses.create.assert_not_called()
    assert mock_post.call_args.kwargs["json"]["model"] == "doubao-seed-2-0-pro-260215"
    assert mock_post.call_args.kwargs["json"]["stream"] is False


def test_deepseek_profile_injects_reasoning_and_thinking_into_chat_payload(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENKB_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("OPENKB_MODEL_REASONING_EFFORT", "high")
    monkeypatch.setenv("OPENKB_DEEPSEEK_THINKING_ENABLED", "true")

    mock_post = MagicMock(return_value=_fake_requests_response("OK"))

    with patch("openkb.llm_runtime.requests.post", mock_post):
        result = completion(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == "deepseek-v4-pro"
    assert payload["reasoning_effort"] == "high"
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["stream"] is False


def test_deepseek_history_patch_preserves_reasoning_content_on_assistant_messages(monkeypatch):
    from agents.models.chatcmpl_converter import Converter

    messages = Converter.items_to_messages(
        [
            {"role": "user", "content": "first"},
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "private reasoning"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            },
            {"role": "user", "content": "next"},
        ],
        model="deepseek-v4-pro",
    )

    assistant = next(message for message in messages if message["role"] == "assistant")
    assert assistant["content"] == "answer"
    assert assistant["reasoning_content"] == "private reasoning"


def test_reasoning_history_patch_does_not_depend_on_deepseek_model_name_or_provider(monkeypatch):
    monkeypatch.delenv("OPENKB_DEEPSEEK_THINKING_ENABLED", raising=False)
    monkeypatch.setenv("OPENKB_MODEL_PROVIDER", "generic")
    from agents.models.chatcmpl_converter import Converter

    messages = Converter.items_to_messages(
        [
            {"role": "user", "content": "first"},
            {
                "type": "reasoning",
                "provider_data": {
                    "model": "litellm/custom_openai/yunzhou-glm",
                    "response_id": "chatcmpl-test",
                },
                "summary": [{"type": "summary_text", "text": "gateway reasoning"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            },
            {"role": "user", "content": "next"},
        ],
        model="yunzhou-glm",
    )

    assistant = next(message for message in messages if message["role"] == "assistant")
    assert assistant["reasoning_content"] == "gateway reasoning"


def test_custom_gateway_completion_raises_clear_error_when_choices_is_null(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": None,
        "usage": {},
        "model": "deepseek-v4-flash",
    }

    with patch("openkb.llm_runtime.requests.post", return_value=response):
        with pytest.raises(ValueError, match="did not return any choices"):
            completion(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": "Reply with OK"}],
            )


def test_custom_gateway_completion_prefers_utf8_bytes_when_response_declares_wrong_encoding(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    expected_text = "????? - ????"
    body = {
        "choices": [
            {
                "message": {
                    "content": expected_text,
                }
            }
        ],
        "usage": {},
    }
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.content = body_bytes
    response.encoding = "ISO-8859-1"
    response.apparent_encoding = "utf-8"
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": body_bytes.decode("iso-8859-1"),
                }
            }
        ],
        "usage": {},
    }

    with patch("openkb.llm_runtime.requests.post", return_value=response):
        result = completion(
            model="gpt-5.2",
            messages=[{"role": "user", "content": "Reply with expected text"}],
        )

    assert result.text == expected_text


def test_completion_retries_custom_gateway_transport_error(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    mock_post = MagicMock(side_effect=[
        requests.exceptions.Timeout("gateway timeout"),
        _fake_requests_response("OK"),
    ])

    with patch("openkb.llm_runtime.requests.post", mock_post), \
         patch("openkb.llm_runtime.time.sleep") as mock_sleep:
        result = completion(
            model="doubao-seed-2-0-pro-260215",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_acompletion_uses_requests_for_custom_gateway_models(monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    mock_post = MagicMock(return_value=_fake_requests_response("OK"))

    with patch("openkb.llm_runtime.requests.post", mock_post):
        result = await acompletion(
            model="doubao-seed-2-0-pro-260215",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    assert mock_post.call_args.kwargs["json"]["model"] == "doubao-seed-2-0-pro-260215"
    assert mock_post.call_args.kwargs["json"]["stream"] is False


def test_completion_records_usage_when_feature_context_is_active(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    _clear_gateway_env(monkeypatch)
    kb_dir = _make_usage_kb(tmp_path)
    usage = import_module("openkb.llm_usage")

    with patch("openkb.llm_runtime.litellm.completion", return_value=_fake_chat_response("OK")):
        with usage.llm_usage_context(kb_dir, "query"):
            result = completion(
                model="gpt-5.4",
                messages=[{"role": "user", "content": "Reply with OK"}],
                max_tokens=16,
            )

    assert result.text == "OK"
    rows = usage.export_usage(kb_dir)
    assert len(rows) == 1
    assert rows[0]["feature"] == "query"
    assert rows[0]["model"] == "gpt-5.4"
    assert rows[0]["status"] == "succeeded"
    assert rows[0]["duration_ms"] >= 0
    assert rows[0]["input_payload"]["messages"][0]["content"] == "Reply with OK"
    assert rows[0]["output_payload"]["text"] == "OK"


@pytest.mark.asyncio
async def test_acompletion_records_usage_when_feature_context_is_active(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    _clear_gateway_env(monkeypatch)
    kb_dir = _make_usage_kb(tmp_path)
    usage = import_module("openkb.llm_usage")

    with patch("openkb.llm_runtime.litellm.acompletion", AsyncMock(return_value=_fake_chat_response("OK"))):
        with usage.llm_usage_context(kb_dir, "compile"):
            result = await acompletion(
                model="gpt-5.4",
                messages=[{"role": "user", "content": "Compile this"}],
            )

    assert result.text == "OK"
    rows = usage.export_usage(kb_dir)
    assert len(rows) == 1
    assert rows[0]["feature"] == "compile"
    assert rows[0]["status"] == "succeeded"
    assert rows[0]["output_payload"]["text"] == "OK"


def test_completion_records_failure_when_feature_context_is_active(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    _clear_gateway_env(monkeypatch)
    kb_dir = _make_usage_kb(tmp_path)
    usage = import_module("openkb.llm_usage")

    with patch("openkb.llm_runtime.litellm.completion", side_effect=RuntimeError("provider down")):
        with pytest.raises(RuntimeError, match="provider down"):
            with usage.llm_usage_context(kb_dir, "lint"):
                completion(
                    model="gpt-5.4",
                    messages=[{"role": "user", "content": "Lint this"}],
                )

    rows = usage.export_usage(kb_dir)
    assert len(rows) == 1
    assert rows[0]["feature"] == "lint"
    assert rows[0]["status"] == "failed"
    assert "provider down" in rows[0]["error"]
    assert rows[0]["duration_ms"] >= 0


def test_completion_without_feature_context_does_not_record_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENKB_WIRE_API", "chat_completions")
    _clear_gateway_env(monkeypatch)
    kb_dir = _make_usage_kb(tmp_path)
    usage = import_module("openkb.llm_usage")

    with patch("openkb.llm_runtime.litellm.completion", return_value=_fake_chat_response("OK")):
        result = completion(
            model="gpt-5.4",
            messages=[{"role": "user", "content": "Reply with OK"}],
        )

    assert result.text == "OK"
    assert usage.export_usage(kb_dir) == []
