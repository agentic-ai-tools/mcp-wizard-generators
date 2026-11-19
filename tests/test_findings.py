"""Secret-exposure findings (ADR-023): one check shared by the API and the generator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_wizard_generators.findings import (
    Finding,
    analyze_operation,
    is_blocking,
    is_sensitive,
    operation_findings,
)
from mcp_wizard_generators.openapi import IN_KEY
from mcp_wizard_generators.python_mcp import generate_python_mcp_result

FIXTURES = Path(__file__).parent / "fixtures"
PETSTORE = json.loads((FIXTURES / "petstore3.openapi.json").read_text(encoding="utf-8"))
DESIGN_REMEDIATION = (
    "Leave this operation unticked unless you need it. If you keep it, run the server only "
    "where request URLs are not logged, and ask the API provider for a login that takes "
    "credentials in a header or request body."
)


def _schema(**props: dict) -> dict:
    return {"type": "object", "properties": props}


def _p(where: str, **schema) -> dict:
    return {"type": "string", **schema, IN_KEY: where}


# --------------------------------------------------------------------- the rule


@pytest.mark.parametrize(
    "name",
    [
        "password",
        "Password",
        "user_password",
        "newPassword",
        "passwd",
        "pwd",
        "passphrase",
        "secret",
        "client_secret",
        "clientSecret",
        "token",
        "access_token",
        "accessToken",
        "refresh-token",
        "api_key",
        "apiKey",
        "X-API-Key",
        "apikey",
        "access_key",
        "AWSAccessKeyId",
        "private_key",
        "credential",
        "credentials",
        "authorization",
        "session_id",
        "sessionId",
        "ssn",
    ],
)
def test_sensitive_names(name: str) -> None:
    assert is_sensitive(name, {"type": "string"}), name


@pytest.mark.parametrize(
    "name",
    [
        "username",
        "status",
        "petId",
        "page_token",
        "pageToken",
        "next_token",
        "nextPageToken",
        "continuation_token",
        "sync_token",
        "token_type",
        "tokenUrl",
        "keyword",
        "key",
        "secretary",
        "author",
        "passage",
        "session",
    ],
)
def test_ordinary_names(name: str) -> None:
    assert not is_sensitive(name, {"type": "string"}), name


def test_schema_marks_make_any_name_sensitive() -> None:
    assert is_sensitive("pin", {"type": "string", "format": "password"})
    assert is_sensitive("value", {"type": "string", "writeOnly": True})
    # Even an exempt pagination name, when the schema says it is a password.
    assert is_sensitive("page_token", {"type": "string", "format": "password"})


# --------------------------------------------------------------------- severity by location


@pytest.mark.parametrize(
    ("where", "code", "severity"),
    [
        ("query", "secret_in_query", "medium"),
        ("path", "secret_in_path", "medium"),
        ("cookie", "secret_in_cookie", "low"),
        ("header", "secret_in_header", "info"),
        ("body", "secret_in_body", "info"),
    ],
)
def test_severity_follows_location(where: str, code: str, severity: str) -> None:
    [finding] = analyze_operation(_schema(password=_p(where)))
    assert (finding.code, finding.severity, finding.parameter, finding.location) == (
        code,
        severity,
        "password",
        where,
    )
    assert is_blocking([finding]) is (severity in ("medium", "high"))


def test_no_location_marker_means_no_finding() -> None:
    # Older IR JSON without locations: nothing to say where it goes, so stay quiet.
    assert analyze_operation(_schema(password={"type": "string"})) == []


def test_one_finding_per_sensitive_parameter() -> None:
    findings = analyze_operation(
        _schema(username=_p("query"), password=_p("query"), token=_p("path"))
    )
    assert [f.parameter for f in findings] == ["password", "token"]


def test_query_finding_text_matches_the_design_fixture() -> None:
    [finding] = analyze_operation(_schema(password=_p("query")))
    assert finding.message == (
        "Sends password in the URL query string, where proxies, servers and logs can record it."
    )
    assert finding.remediation == DESIGN_REMEDIATION
    assert finding.to_dict() == {
        "code": "secret_in_query",
        "severity": "medium",
        "parameter": "password",
        "location": "query",
        "message": finding.message,
        "remediation": DESIGN_REMEDIATION,
    }


# --------------------------------------------------------------------- API helper


def test_operation_findings_for_petstore() -> None:
    found = operation_findings(PETSTORE)
    # Keyed by the same ids the API catalog uses; info-level findings omitted.
    assert set(found) == {"loginUser"}
    [finding] = found["loginUser"]
    assert finding["code"] == "secret_in_query"
    assert finding["parameter"] == "password"
    assert finding["severity"] == "medium"


def test_operation_findings_include_info_on_request() -> None:
    found = operation_findings(PETSTORE, include_info=True)
    # deletePet sends api_key as a header: fine, but redacted later.
    assert [f["code"] for f in found["deletePet"]] == ["secret_in_header"]
    assert "loginUser" in found


def test_operation_findings_never_raise_on_junk() -> None:
    assert operation_findings({"openapi": "3.0.0", "paths": {"/x": "nope"}}) == {}
    assert operation_findings({}) == {}


def test_finding_is_frozen() -> None:
    with pytest.raises(AttributeError):
        Finding("c", "low", "p", "query", "m", "r").code = "x"  # type: ignore[misc]


# --------------------------------------------------------------------- generated zip


def _files(operations: list[str] | None, **kwargs) -> dict[str, str]:
    result = generate_python_mcp_result(PETSTORE, operations, package_name="petstore_mcp", **kwargs)
    return {e.path: e.content for e in result.tree.entries}


def test_zip_with_a_flagged_tool_carries_the_finding() -> None:
    files = _files(["loginUser", "getPetById"])
    manifest = json.loads(files["generation-manifest.json"])
    assert manifest["findings"] == [
        {
            "tool": "loginuser",
            "operationId": "loginUser",
            **operation_findings(PETSTORE)["loginUser"][0],
        }
    ]
    notes = files["SECURITY-NOTES.md"]
    assert "loginuser" in notes
    assert "password" in notes
    assert "Operations step" in notes
    assert "server.py" in notes
    assert "SECURITY-NOTES.md" in files["README.md"]


def test_flagged_tool_description_warns_the_model() -> None:
    server = _files(["loginUser"])["src/petstore_mcp/server.py"]
    assert (
        'description="WARNING: sends password in the URL query string (see SECURITY-NOTES.md). '
        "Logs user into the system."
    ) in server


def test_clean_zip_has_no_security_notes() -> None:
    files = _files(["getPetById"])
    assert "SECURITY-NOTES.md" not in files
    assert json.loads(files["generation-manifest.json"])["findings"] == []
    assert "WARNING" not in files["src/petstore_mcp/server.py"]


def test_api_key_in_query_is_an_auth_note_not_an_operation_finding() -> None:
    files = _files(
        ["getPetById"],
        security={"authMode": "api_key", "apiKey": {"location": "query", "name": "api_key"}},
    )
    notes = files["SECURITY-NOTES.md"]
    assert "API key" in notes
    assert "query string" in notes
    assert json.loads(files["generation-manifest.json"])["findings"] == []


# --------------------------------------------------------------------- malformed input


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"properties": None},
        {"properties": ["password"]},
        "not a schema",
    ],
)
def test_schemas_without_properties_have_no_findings(schema) -> None:
    assert analyze_operation(schema) == []  # type: ignore[arg-type]


def test_non_dict_properties_are_skipped() -> None:
    schema = _schema(password="string", token=_p("query"))  # type: ignore[arg-type]
    assert [f.parameter for f in analyze_operation(schema)] == ["token"]


def test_operation_findings_survive_a_converter_failure(monkeypatch) -> None:
    import mcp_wizard_generators.findings as findings

    def boom(_spec):
        raise ValueError("bad document")

    monkeypatch.setattr(findings, "openapi_to_ir", boom)
    assert operation_findings(PETSTORE) == {}


def test_operation_findings_accept_a_non_dict() -> None:
    assert operation_findings(None) == {}  # type: ignore[arg-type]


def test_short_form_per_location_and_fallback() -> None:
    by_location = {
        f.location: f.short
        for where in ("query", "path", "cookie", "header", "body")
        for f in analyze_operation(_schema(password=_p(where)))
    }
    assert by_location == {
        "query": "sends password in the URL query string",
        "path": "puts password in the URL path",
        "cookie": "sends password in a cookie",
        "header": "sends password in a header",
        "body": "sends password in the request body",
    }
    custom = Finding("custom", "low", None, None, "Something odd.", "Look into it.")
    assert custom.short == "Something odd."
