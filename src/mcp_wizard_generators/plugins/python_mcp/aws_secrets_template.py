"""Upstream API credentials from AWS Secrets Manager (design ADR-026, ADR-017).

Emitted when a deployment switches on the ``aws-secrets`` server feature (the
AgentCore targets). The secret is read at startup with the runtime's IAM role, so
credentials never sit in runtime environment variables or in Terraform state.
"""

from __future__ import annotations

AWS_SECRETS_MODULE = r'''"""Upstream API credentials from AWS Secrets Manager.

Set API_SECRETS_ARN to a secret whose value is a JSON object, for example
{"API_KEY": "..."}. At startup the server reads it with its IAM role and fills
in the credential variables listed in CREDENTIALS that are not already set, so
an explicit environment variable always wins. Values are never logged.
"""

from __future__ import annotations

import json
import os
import re

CREDENTIALS: tuple[str, ...] = __CREDENTIALS__
_ARN = re.compile(r"arn:aws[a-z-]*:secretsmanager:([a-z0-9-]+):\d{12}:secret:[\w/+=.@-]+")


class SecretsError(RuntimeError):
    """The secret cannot be read or has the wrong shape. The message says how to fix it."""


def load_api_secrets() -> list[str]:
    """Fill missing credential variables from the secret; return the names that were set."""
    arn = os.environ.get("API_SECRETS_ARN", "").strip()
    if not arn:
        return []
    match = _ARN.fullmatch(arn)
    if not match:
        raise SecretsError(
            "API_SECRETS_ARN must be a Secrets Manager secret ARN "
            "(arn:aws:secretsmanager:<region>:<account>:secret:<name>)"
        )
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        raise SecretsError(
            "boto3 is not installed: pip install --require-hashes -r requirements-aws.lock"
        ) from None
    client = boto3.client("secretsmanager", region_name=match.group(1))
    try:
        response = client.get_secret_value(SecretId=arn)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "AccessDeniedException":
            raise SecretsError(
                f"this server's IAM role is not allowed to read {arn}; "
                "grant it secretsmanager:GetSecretValue on that secret"
            ) from None
        if code == "ResourceNotFoundException":
            raise SecretsError(
                f"the secret {arn} does not exist or has no value yet; "
                "set one with aws secretsmanager put-secret-value"
            ) from None
        raise SecretsError(f"reading {arn} failed: {code or 'unknown error'}") from None
    except BotoCoreError as exc:
        raise SecretsError(f"reading {arn} failed: {type(exc).__name__}") from None
    text = response.get("SecretString")
    try:
        values = json.loads(text) if isinstance(text, str) else None
    except json.JSONDecodeError:
        values = None
    if not isinstance(values, dict):
        raise SecretsError(
            f'the secret {arn} must hold a JSON object such as {{"API_KEY": "..."}}'
        )
    loaded: list[str] = []
    for name in CREDENTIALS:
        if name not in values or os.environ.get(name):
            continue
        value = values[name]
        if not isinstance(value, str):
            raise SecretsError(f"{name} in the secret must be a string")
        os.environ[name] = value
        loaded.append(name)
    return loaded
'''

TEST_AWS_SECRETS = '''\
"""Credentials from AWS Secrets Manager, with boto3 faked (no AWS calls)."""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

from __PKG__ import aws_secrets

ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:api-AbCdEf"


class ClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


@pytest.fixture
def fake_aws(monkeypatch: pytest.MonkeyPatch) -> dict:
    state: dict = {"secret": {}, "error": ""}

    class Client:
        def get_secret_value(self, SecretId: str) -> dict:  # noqa: N803 - boto3 casing
            if state["error"]:
                raise ClientError(state["error"])
            return {"SecretString": json.dumps(state["secret"])}

    boto3 = types.ModuleType("boto3")
    boto3.client = lambda service, region_name=None: Client()
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = ClientError
    exceptions.BotoCoreError = type("BotoCoreError", (Exception,), {})
    botocore = types.ModuleType("botocore")
    botocore.exceptions = exceptions
    for name, module in (("boto3", boto3), ("botocore", botocore), ("botocore.exceptions", exceptions)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("API_SECRETS_ARN", ARN)
    for name in aws_secrets.CREDENTIALS:
        # setenv first so the original state is restored even after the loader sets it.
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    return state


def test_without_an_arn_nothing_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_SECRETS_ARN", raising=False)
    assert aws_secrets.load_api_secrets() == []


def test_credentials_are_filled_in(fake_aws: dict) -> None:
    fake_aws["secret"] = {name: "value-" + name for name in aws_secrets.CREDENTIALS}
    assert aws_secrets.load_api_secrets() == list(aws_secrets.CREDENTIALS)
    for name in aws_secrets.CREDENTIALS:
        assert os.environ[name] == "value-" + name


def test_access_denied_is_explained(fake_aws: dict) -> None:
    fake_aws["error"] = "AccessDeniedException"
    with pytest.raises(aws_secrets.SecretsError, match="not allowed"):
        aws_secrets.load_api_secrets()
'''

__all__ = ["AWS_SECRETS_MODULE", "TEST_AWS_SECRETS"]
