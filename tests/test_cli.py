import os
import json
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from openkb.cli import cli
from openkb.schema import AGENTS_MD


def test_init_creates_structure(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path), \
         patch("openkb.cli.register_kb"):
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0

        from pathlib import Path
        cwd = Path(".")

        # Directories
        assert (cwd / "raw").is_dir()
        assert (cwd / "wiki" / "sources" / "images").is_dir()
        assert (cwd / "wiki" / "summaries").is_dir()
        assert (cwd / "wiki" / "concepts").is_dir()
        assert (cwd / "wiki" / "companies").is_dir()
        assert (cwd / ".openkb").is_dir()

        # Files
        assert (cwd / "wiki" / "AGENTS.md").is_file()
        assert (cwd / "wiki" / "log.md").is_file()
        assert (cwd / "wiki" / "index.md").is_file()
        assert (cwd / ".openkb" / "config.yaml").is_file()
        assert (cwd / ".openkb" / "hashes.json").is_file()

        # hashes.json is empty object
        hashes = json.loads((cwd / ".openkb" / "hashes.json").read_text())
        assert hashes == {}

        config = json.loads(json.dumps(yaml.safe_load((cwd / ".openkb" / "config.yaml").read_text())))
        assert config["wire_api"] == "responses"

        # index.md header
        index_content = (cwd / "wiki" / "index.md").read_text()
        assert index_content == "# Knowledge Base Index\n\n## Documents\n\n## Companies\n\n## Concepts\n\n## Explorations\n"


def test_init_schema_content(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path), \
         patch("openkb.cli.register_kb"):
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0

        from pathlib import Path
        agents_content = Path("wiki/AGENTS.md").read_text(encoding="utf-8")
        assert agents_content == AGENTS_MD
        assert "companies/" in agents_content
        assert "Company Page" in agents_content


def test_init_already_exists(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path), \
         patch("openkb.cli.register_kb"):
        # First run should succeed
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0

        # Second run should print already initialized message
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "already initialized" in result.output


def test_setup_llm_key_reads_wire_api_from_kb_config(tmp_path, monkeypatch):
    from openkb import config as config_module
    from openkb.cli import _setup_llm_key

    kb_dir = tmp_path / "kb"
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir(parents=True)
    (openkb_dir / "config.yaml").write_text(
        "model: gpt-5.4\nlanguage: zh\npageindex_threshold: 20\nwire_api: responses\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENKB_WIRE_API", raising=False)
    monkeypatch.delenv("OPENAI_WIRE_API", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_DIR", tmp_path / "global-config")

    with patch("openkb.cli.configure_runtime") as mock_configure:
        _setup_llm_key(kb_dir)

    assert os.environ["OPENKB_WIRE_API"] == "responses"
    mock_configure.assert_called_once_with("gpt-5.4")


def test_setup_llm_key_reads_base_url_from_kb_config(tmp_path, monkeypatch):
    from openkb import config as config_module
    from openkb.cli import _setup_llm_key

    kb_dir = tmp_path / "kb"
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir(parents=True)
    (openkb_dir / "config.yaml").write_text(
        "model: gpt-5.4\nlanguage: zh\npageindex_threshold: 20\nwire_api: responses\nbase_url: https://gateway.example.com\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_DIR", tmp_path / "global-config")

    with patch("openkb.cli.configure_runtime") as mock_configure:
        _setup_llm_key(kb_dir)

    assert os.environ["OPENAI_BASE_URL"] == "https://gateway.example.com/v1"
    assert os.environ["OPENAI_API_BASE"] == "https://gateway.example.com/v1"
    mock_configure.assert_called_once_with("gpt-5.4")


def test_setup_llm_key_refreshes_runtime_env_for_each_kb(tmp_path, monkeypatch):
    from openkb import config as config_module
    from openkb.cli import _setup_llm_key

    kb_responses = tmp_path / "kb-responses"
    kb_responses_openkb = kb_responses / ".openkb"
    kb_responses_openkb.mkdir(parents=True)
    (kb_responses_openkb / "config.yaml").write_text(
        "model: gpt-5.4\nwire_api: responses\nbase_url: https://responses.example.com\n",
        encoding="utf-8",
    )
    (kb_responses / ".env").write_text("LLM_API_KEY=sk-responses\n", encoding="utf-8")

    kb_chat = tmp_path / "kb-chat"
    kb_chat_openkb = kb_chat / ".openkb"
    kb_chat_openkb.mkdir(parents=True)
    (kb_chat_openkb / "config.yaml").write_text(
        "model: gpt-5.4\nwire_api: chat_completions\nbase_url: https://chat.example.com/v1\n",
        encoding="utf-8",
    )
    (kb_chat / ".env").write_text("LLM_API_KEY=sk-chat\n", encoding="utf-8")

    for name in [
        "OPENKB_WIRE_API",
        "OPENAI_WIRE_API",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_DIR", tmp_path / "global-config")

    with patch("openkb.cli.configure_runtime"):
        _setup_llm_key(kb_responses)
        assert os.environ["OPENKB_WIRE_API"] == "responses"
        assert os.environ["OPENAI_BASE_URL"] == "https://responses.example.com/v1"
        assert os.environ["OPENAI_API_KEY"] == "sk-responses"

        _setup_llm_key(kb_chat)
        assert os.environ["OPENKB_WIRE_API"] == "chat_completions"
        assert os.environ["OPENAI_BASE_URL"] == "https://chat.example.com/v1"
        assert os.environ["OPENAI_API_KEY"] == "sk-chat"


def test_init_keeps_chat_completions_for_non_gpt5_models(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path), \
         patch("openkb.cli.register_kb"):
        result = runner.invoke(cli, ["init"], input="anthropic/claude-sonnet-4-6\n\n")
        assert result.exit_code == 0

        from pathlib import Path
        config = json.loads(json.dumps(yaml.safe_load((Path(".openkb") / "config.yaml").read_text())))
        assert config["model"] == "anthropic/claude-sonnet-4-6"
        assert config["wire_api"] == "chat_completions"
