"""Session primitives use synthetic credentials and temporary records only."""

import base64
import copy
import json
import os
from uuid import uuid4

import pytest

from claude_swap import codex_session
from claude_swap.exceptions import SessionError


def auth(**changes):
    """Build a synthetic auth document with optional nested claim changes."""
    nested = dict(
        chatgpt_user_id="user",
        chatgpt_account_id="workspace",
        chatgpt_plan_type="plus",
    )
    nested.update(changes)
    claims = {"email": " User@EXAMPLE.org ", "https://api.openai.com/auth": nested}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return {
        "tokens": dict(
            id_token=f"e30.{payload}.sig",
            access_token="synthetic",
            refresh_token="synthetic",
            account_id="workspace",
        ),
    }


def record(kind, tmp_path):
    """Build a valid synthetic record of the requested schema kind."""
    data = dict(
        schemaVersion=1,
        home=str(tmp_path / "session"),
        accountNumber="1",
        identity=codex_session.extract_identity(auth())[0],
    )
    if kind == "manifest":
        data.update(
            sourceHome=str(tmp_path / "default"),
            baselineFingerprint="a" * 64,
            bootstrapState="ready",
            sharedItems={},
        )
    elif kind == "stale":
        data.update(oldFingerprint="a" * 64, newFingerprint="b" * 64, reason="refresh")
    else:
        data = dict(
            schemaVersion=1,
            home=str(tmp_path / "session"),
            runId=str(uuid4()),
            child=None,
            supervisor=dict(pid=1, start=dict(kind="linux", bootId="boot", ticks="0")),
            phase="starting",
            shared=False,
            jobName=None,
            exitCode=None,
        )
    return data


def test_identity_and_fingerprint():
    """Check custody comparisons and complete semantic auth fingerprints."""
    original = auth()
    identity, plan = codex_session.extract_identity(original)
    assert identity["email"] == "user@example.org" and plan == "plus"
    other_identity = codex_session.extract_identity(auth(chatgpt_user_id="other"))[0]
    assert not codex_session.same_identity(identity, other_identity)
    owned = dict(identity, loginOwnerId="one")
    assert codex_session.same_identity(identity, owned)
    assert not codex_session.same_identity(owned, dict(identity, loginOwnerId="two"))
    changed = copy.deepcopy(original)
    changed["tokens"]["login_owner_id"] = "one"
    assert codex_session.extract_identity(changed)[0] == owned
    reformatted = json.loads(json.dumps(original, indent=2))
    assert codex_session.auth_fingerprint(original) == codex_session.auth_fingerprint(reformatted)
    changed = dict(original, unknown={"unicode": "ä", "oauth_provenance": "preserved"})
    assert codex_session.auth_fingerprint(changed) != codex_session.auth_fingerprint(original)
    preserved = copy.deepcopy(changed)
    codex_session.extract_identity(changed)
    codex_session.auth_fingerprint(changed)
    assert changed == preserved
    for invalid_auth in (None, {"x": float("nan")}, {"x": object()}):
        with pytest.raises(SessionError):
            codex_session.auth_fingerprint(invalid_auth)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda data: data.update(auth_mode="api"), id="api-mode"),
        pytest.param(lambda data: data.update(OPENAI_API_KEY="synthetic"), id="api-key"),
        pytest.param(
            lambda data: data["tokens"].update(account_id="other"), id="workspace-mismatch",
        ),
        pytest.param(
            lambda data: data["tokens"].pop("refresh_token"), id="missing-refresh-token",
        ),
        pytest.param(lambda data: data["tokens"].update(id_token="bad"), id="malformed-token"),
        pytest.param(lambda data: data["tokens"].update(login_owner_id=""), id="empty-owner"),
    ],
)
def test_invalid_auth(mutate):
    """Reject invalid credentials without including their contents in the error."""
    data = auth()
    mutate(data)
    with pytest.raises(SessionError, match="^Invalid ChatGPT session identity.$"):
        codex_session.extract_identity(data)


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"chatgpt_user_id": None}, id="missing-user"),
        pytest.param({"chatgpt_account_id": ""}, id="empty-workspace"),
        pytest.param({"user_id": "other"}, id="contradictory-user"),
    ],
)
def test_invalid_nested_claims(changes):
    """Reject incomplete or contradictory nested identity claims."""
    with pytest.raises(SessionError):
        codex_session.extract_identity(auth(**changes))


@pytest.mark.parametrize("plan", [None, "unknown", "business", "enterprise", "edu", "sci_plus"])
def test_plan_gate(plan):
    """Reject unsupported plans while allowing the known team plan."""
    assert not codex_session.session_plan_supported(plan)
    assert codex_session.session_plan_supported("team")


@pytest.mark.parametrize("kind", ["manifest", "run", "stale"])
def test_schema_and_load_failures(kind, tmp_path, monkeypatch):
    """Distinguish missing, malformed and unreadable records for every schema."""
    data = record(kind, tmp_path)
    path = tmp_path / (data.get("runId", kind) + ".json")
    assert codex_session.load_record(path, kind) is None
    path.write_text(json.dumps(data))
    assert codex_session.load_record(path, kind) == data
    for field in data:
        without_field = {key: value for key, value in data.items() if key != field}
        with pytest.raises(SessionError):
            codex_session.validate_record(without_field, kind, run_id=path.stem)
        for invalid_value in (None, [], True):
            invalid_record = dict(data, **{field: invalid_value})
            unchanged = type(invalid_value) is type(data[field]) and invalid_value == data[field]
            valid_shared = field == "shared" and invalid_value is True
            if unchanged or valid_shared:
                continue
            with pytest.raises(SessionError):
                codex_session.validate_record(invalid_record, kind, run_id=path.stem)
    for text in ('{', '[]', '{"schemaVersion":1,"schemaVersion":1}', '{"x":NaN}'):
        path.write_text(text)
        with pytest.raises(SessionError, match="Malformed"):
            codex_session.load_record(path, kind)

    def deny_read(*args):
        raise PermissionError()

    monkeypatch.setattr(codex_session.os, "open", deny_read)
    with pytest.raises(SessionError, match="Unreadable"):
        codex_session.load_record(path, kind)


@pytest.mark.parametrize(
    "start",
    [
        pytest.param(dict(kind="linux", bootId="b", ticks="1"), id="linux"),
        pytest.param(dict(kind="macos", bootId="b", uniqueId="1"), id="macos"),
        pytest.param(dict(kind="windows", creationTime100ns="1"), id="windows"),
    ],
)
def test_process_ranges(start):
    """Accept each process identity variant and reject invalid process IDs."""
    codex_session._process(dict(pid=1, start=start))
    for pid in (0, -1, True, None, 2**32):
        with pytest.raises(SessionError):
            codex_session._process(dict(pid=pid, start=start))


@pytest.mark.parametrize(
    "kind,key,value",
    [
        pytest.param("manifest", "schemaVersion", 2, id="unknown-schema"),
        pytest.param("manifest", "accountNumber", "0", id="zero-account"),
        pytest.param("manifest", "home", "/a/../b", id="noncanonical-home"),
        pytest.param("manifest", "baselineFingerprint", "A" * 64, id="uppercase-fingerprint"),
        pytest.param("manifest", "bootstrapState", "unknown", id="unknown-bootstrap-state"),
        pytest.param("stale", "reason", "unknown", id="unknown-stale-reason"),
        pytest.param("run", "phase", "unknown", id="unknown-run-phase"),
        pytest.param("run", "runId", str(uuid4()), id="run-filename-mismatch"),
        pytest.param(
            "manifest",
            "sharedItems",
            {"../escape": {"source": "/source", "mode": "copy"}},
            id="escaping-shared-path",
        ),
    ],
)
def test_schema_values(kind, key, value, tmp_path):
    """Reject invalid schema versions, values and paths."""
    data = record(kind, tmp_path)
    run_id = data.get("runId")
    if key == "home":
        value = str(tmp_path) + os.sep + "a" + os.sep + ".." + os.sep + "b"
    elif key == "sharedItems":
        value = {"../escape": {"source": str(tmp_path / "source"), "mode": "copy"}}
    data[key] = value
    with pytest.raises(SessionError):
        codex_session.validate_record(data, kind, run_id=run_id)


def test_nonordinary_record(tmp_path):
    """Reject a dangling symlink instead of treating it as a missing record."""
    path = tmp_path / "link"
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(SessionError, match="Malformed"):
        codex_session.load_record(path, "manifest")


def replace_claims(data, claims):
    """Replace only the synthetic JWT claims, preserving the auth envelope."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    data["tokens"]["id_token"] = f"e30.{payload}.sig"
    return data


@pytest.mark.parametrize(
    "user_fallback,email_fallback", [(True, False), (False, True), (True, True)],
)
def test_identity_fallbacks(user_fallback, email_fallback):
    """Use Codex's nested user and profile email fallbacks."""
    data = auth()
    payload = data["tokens"]["id_token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    nested = claims["https://api.openai.com/auth"]
    if user_fallback:
        nested["user_id"] = nested.pop("chatgpt_user_id")
    if email_fallback:
        claims["https://api.openai.com/profile"] = {"email": claims.pop("email")}
    assert codex_session.extract_identity(replace_claims(data, claims))[0] == (
        codex_session.extract_identity(auth())[0]
    )


@pytest.mark.parametrize("field", ["chatgpt_user_id", "chatgpt_account_id"])
def test_untrimmed_ids(field):
    """Reject padded identifiers even when the workspace agrees with the envelope."""
    data = auth(**{field: " padded "})
    if field == "chatgpt_account_id":
        data["tokens"]["account_id"] = " padded "
    with pytest.raises(SessionError):
        codex_session.extract_identity(data)


def test_contradictory_email():
    """Reject conflicting top-level and profile email claims."""
    data = auth()
    claims = {"email": "one@example.org", "https://api.openai.com/profile": {
        "email": "two@example.org"}, "https://api.openai.com/auth": {
        "chatgpt_user_id": "user", "chatgpt_account_id": "workspace"}}
    with pytest.raises(SessionError):
        codex_session.extract_identity(replace_claims(data, claims))


@pytest.mark.parametrize("token", [
    ".e30.sig", "e30.e30.", "e30..sig", "!.e30.sig", "e30.e30.!",
    "e30.e30=.sig", "e30.e31.sig", "e30.e30.sig.extra",
])
def test_jwt_shape(token):
    """Reject empty, invalid, padded and noncanonical JWT segments."""
    data = auth()
    data["tokens"]["id_token"] = token
    with pytest.raises(SessionError):
        codex_session.extract_identity(data)


@pytest.mark.parametrize("location", ["record", "jwt", "fingerprint"])
def test_deep_json(location, tmp_path, monkeypatch):
    """Translate parser and serializer recursion failures to safe session errors."""
    deeply_nested = "[" * 10000 + "0" + "]" * 10000
    with pytest.raises(SessionError):
        if location == "record":
            path = tmp_path / "deep.json"
            path.write_text(deeply_nested)
            codex_session.load_record(path, "manifest")
        elif location == "jwt":
            data = auth()
            payload = base64.urlsafe_b64encode(deeply_nested.encode()).decode().rstrip("=")
            data["tokens"]["id_token"] = f"e30.{payload}.sig"
            codex_session.extract_identity(data)
        else:
            document = {}
            current = document
            for _ in range(10000):
                current["next"] = {}
                current = current["next"]
            def recursion_failure(*args, **kwargs):
                raise RecursionError()

            monkeypatch.setattr(codex_session.json, "dumps", recursion_failure)
            codex_session.auth_fingerprint(document)


@pytest.mark.parametrize("value", [None, 1, "bad", "2026-02-30T12:00:00Z"])
def test_invalid_last_refresh(value):
    """Reject timestamps Codex cannot deserialize."""
    data = dict(auth(), last_refresh=value)
    with pytest.raises(SessionError):
        codex_session.extract_identity(data)


@pytest.mark.parametrize("value", [None, [], {}, {"issuer": 1, "client_id": "c"}])
def test_invalid_provenance(value):
    """Require the complete typed OAuth provenance object when present."""
    data = auth()
    data["tokens"]["oauth_provenance"] = value
    with pytest.raises(SessionError):
        codex_session.extract_identity(data)


def test_valid_auth_metadata():
    """Accept a valid timestamp and complete provenance without dropping unknown fields."""
    data = dict(auth(), last_refresh="2026-10-25T12:34:56.123Z")
    data["tokens"]["oauth_provenance"] = {"issuer": "https://example.org", "client_id": "c"}
    assert codex_session.extract_identity(data) == codex_session.extract_identity(auth())


@pytest.mark.parametrize("destination", [
    "config.toml", "AGENTS.md", "AGENTS.override.md", "rules", "prompts", "skills/example",
])
def test_shareable_destinations(destination, tmp_path):
    """Allow exactly the supported definitions and individual user skills."""
    data = record("manifest", tmp_path)
    data["sharedItems"] = {destination: {"source": str(tmp_path / "source"), "mode": "copy"}}
    assert codex_session.validate_record(data, "manifest") == data


@pytest.mark.parametrize("destination", [
    "auth.json", "skills", "skills/.system", "skills/example/nested", "plugins", "sessions",
])
def test_unshareable_destinations(destination, tmp_path):
    """Reject private state and unsupported skill destinations."""
    data = record("manifest", tmp_path)
    data["sharedItems"] = {destination: {"source": str(tmp_path / "source"), "mode": "copy"}}
    with pytest.raises(SessionError):
        codex_session.validate_record(data, "manifest")


@pytest.mark.parametrize("kind", ["manifest", "stale"])
def test_leading_zero_account(kind, tmp_path):
    """Reject noncanonical account numbers in both custody schemas."""
    data = record(kind, tmp_path)
    data["accountNumber"] = "01"
    with pytest.raises(SessionError):
        codex_session.validate_record(data, kind)


@pytest.mark.parametrize("exit_code", [-(2**31), 0, 2**31 - 1, 2**32 - 1, -(2**31) - 1, 2**32])
def test_windows_run_ranges(exit_code, tmp_path):
    """Validate a positive Windows record and both signed and native exit boundaries."""
    data = record("run", tmp_path)
    data["supervisor"] = {"pid": 2**32 - 1, "start": {
        "kind": "windows", "creationTime100ns": "123"}}
    data["jobName"] = "Local\\ccswap-codex-" + data["runId"]
    data["exitCode"] = exit_code
    if -(2**31) <= exit_code <= 2**32 - 1:
        assert codex_session.validate_record(data, "run", run_id=data["runId"]) == data
    else:
        with pytest.raises(SessionError):
            codex_session.validate_record(data, "run", run_id=data["runId"])


@pytest.mark.parametrize("kind", ["directory", "fifo", "symlink"])
def test_nonordinary_open(kind, tmp_path):
    """Reject nonordinary files without blocking or following a link to valid content."""
    path = tmp_path / "record.json"
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        if os.name != "posix":
            pytest.skip("POSIX FIFO")
        os.mkfifo(path)
    else:
        target = tmp_path / "valid.json"
        target.write_text(json.dumps(record("manifest", tmp_path)))
        path.symlink_to(target)
    with pytest.raises(SessionError, match="Malformed"):
        codex_session.load_record(path, "manifest")


@pytest.mark.parametrize("plan", [
    "free", "go", "plus", "pro", "prolite", "promax", "team",
    "self_serve_business_prolite", "self_serve_business_usage_based",
])
def test_all_allowed_plans(plan):
    """Accept every known plan outside the cloud-configuration eligibility set."""
    assert codex_session.session_plan_supported(plan)
    assert not codex_session.session_plan_supported(plan.upper())


@pytest.mark.parametrize("plan", [
    "business", "ent26", "enterprise_cbp_automation", "enterprise_cbp_usage_based",
    "enterprise", "hc", "edu", "education", "edu_plus", "edu_pro", "sci", "sci_plus",
    "Free", "PLUS", "Team", "unknown", None,
])
def test_all_cloud_and_unknown_plans(plan):
    """Reject cloud-eligible plans, aliases and unrecognized case variants."""
    assert not codex_session.session_plan_supported(plan)


def test_fingerprint_golden_and_key_order():
    """Pin canonical UTF-8 serialization and prove key-order independence."""
    expected = "688218a4131df1c29ea57a1903c5145932e1db0b48a723a989731375ab11da9b"
    assert codex_session.auth_fingerprint({"z": "ä", "a": 1}) == expected
    assert codex_session.auth_fingerprint({"a": 1, "z": "ä"}) == expected


@pytest.mark.parametrize("change", ["padding", "trailing-bits"])
def test_noncanonical_valid_payload(change):
    """Reject noncanonical encoding even when the decoded claims are valid."""
    data = auth()
    payload = data["tokens"]["id_token"].split(".")[1]
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    while len(decoded) % 3 != 1:
        decoded += b" "
    payload = base64.urlsafe_b64encode(decoded).decode().rstrip("=")
    if change == "padding":
        payload += "=="
    else:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        payload = payload[:-1] + alphabet[alphabet.index(payload[-1]) + 1]
    data["tokens"]["id_token"] = f"e30.{payload}.sig"
    with pytest.raises(SessionError):
        codex_session.extract_identity(data)


def test_matching_fallback_and_owner_whitespace():
    """Accept agreeing user claims and reject padded optional owner identifiers."""
    assert codex_session.extract_identity(auth(user_id="user")) == (
        codex_session.extract_identity(auth())
    )
    data = auth()
    data["tokens"]["login_owner_id"] = " padded "
    with pytest.raises(SessionError):
        codex_session.extract_identity(data)
