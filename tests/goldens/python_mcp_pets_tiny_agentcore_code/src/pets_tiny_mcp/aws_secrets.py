"""Upstream API credentials from AWS Secrets Manager.

Set API_SECRETS_ARN to a secret whose value is a JSON object, for example
{"API_KEY": "..."}. At startup the server reads it with its IAM role and fills
in the credential variables listed in CREDENTIALS that are not already set, so
an explicit environment variable always wins. Values are never logged.
"""

from __future__ import annotations

import json
import os
import re

CREDENTIALS: tuple[str, ...] = ('API_KEY',)
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
