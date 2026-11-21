"""Credentials from AWS Secrets Manager, with boto3 faked (no AWS calls)."""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

from pets_tiny_mcp import aws_secrets

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
