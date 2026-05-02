from __future__ import annotations

import pytest
from click.testing import CliRunner

from openkb.cli import cli


def test_client_command_is_registered_in_help():
    result = CliRunner().invoke(cli, ["client", "--help"])

    assert result.exit_code == 0
    assert "--port" in result.output
    assert "--no-browser" in result.output


def test_client_command_reports_missing_optional_dependencies():
    from openkb.client import server

    def raise_missing_dependency():
        raise server.ClientDependencyError("client dependencies are not installed")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(server, "_import_web_dependencies", raise_missing_dependency)
        result = CliRunner().invoke(cli, ["client", "--no-browser"])

    assert result.exit_code != 0
    assert "pip install" in result.output
    assert "openkb[client]" in result.output
