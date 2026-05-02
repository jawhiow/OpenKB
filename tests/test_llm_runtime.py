from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
