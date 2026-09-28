"""Diagnostics report local state without echoing credential-like URL parts."""

from __future__ import annotations

import json

from claude_swap import cli, diagnostics


def test_doctor_redacts_endpoint_secrets_and_reports_duplicate_accounts(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(diagnostics, "_cli_check",
                        lambda name: diagnostics.Check(name, "ok", "1.2.3"))
    (tmp_path / "sequence.json").write_text(json.dumps({"sequence": [1, 2], "accounts": {
        "1": {"email": "same@example.test", "organizationUuid": "org"},
        "2": {"email": "same@example.test", "organizationUuid": "org"},
    }}), encoding="utf-8")
    checks = diagnostics.diagnose(tmp_path, env={
        "ANTHROPIC_BASE_URL": "https://name:topsecret@gateway.test/private-token?key=secret",
        "HTTPS_PROXY": "http://person:another-secret@proxy.test:8080/hidden",
        "ANTHROPIC_API_KEY": "secret-key-should-not-appear",
    })
    text = json.dumps([check.to_dict() for check in checks])
    assert "topsecret" not in text
    assert "another-secret" not in text
    assert "private-token" not in text
    assert "secret-key-should-not-appear" not in text
    assert "https://gateway.test" in text
    assert "http://proxy.test:8080" in text
    assert next(c for c in checks if c.name == "accounts").status == "warning"


def test_doctor_json_marks_corrupt_roster_as_error(tmp_path, monkeypatch, capsys):
    (tmp_path / "sequence.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "_cli_check",
                        lambda name: diagnostics.Check(name, "ok", "1.2.3"))
    monkeypatch.setattr(cli.paths, "get_backup_root", lambda: tmp_path)
    try:
        cli._doctor_command(["--json"])
    except SystemExit as exc:
        assert exc.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert any(check["name"] == "accounts" and check["status"] == "error"
               for check in payload["checks"])


def test_doctor_warns_about_competing_cli_installs(tmp_path, monkeypatch):
    import os
    import sys

    monkeypatch.setattr(diagnostics, "_cli_check",
                        lambda name: diagnostics.Check(name, "ok", "1.2.3"))
    native, npm, other = tmp_path / "native", tmp_path / "npm", tmp_path / "other"
    for folder in (native, npm, other):
        folder.mkdir()
    windows = sys.platform == "win32"
    for path in (native / ("claude.exe" if windows else "claude"),
                 npm / ("claude.cmd" if windows else "claude"),
                 other / ("codex.exe" if windows else "codex"),
                 # a PowerShell shim alone is not a PATHEXT hit
                 other / "claude.ps1"):
        path.write_text("", encoding="utf-8")
        path.chmod(0o755)
    env = {"PATH": os.pathsep.join([str(native), str(npm), str(native), str(other)]),
           "PATHEXT": ".COM;.EXE;.BAT;.CMD"}

    checks = {c.name: c for c in diagnostics.diagnose(tmp_path / "store", env=env)}

    installs = checks["claude installs"]
    assert installs.status == "warning"
    assert installs.detail.startswith("2 on PATH, first wins: ")
    assert installs.detail.index("native") < installs.detail.index("npm")
    assert "codex installs" not in checks
