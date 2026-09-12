"""Secrets stay out of logs and configuration; switches fail closed."""

from __future__ import annotations

import logging

import boto3
import httpx
import pytest
from moto import mock_aws

from doorstep_agent.cloud.logs import configure_logging
from doorstep_agent.cloud.ssm import RUNTIME_SECRETS, SsmFlags, hydrate_env
from doorstep_agent.config import running_in_aws
from doorstep_agent.notify.telegram import Bot, TelegramError

FAKE_TOKEN = "123456:SECRET-token-that-must-never-be-logged"


@pytest.fixture
def ssm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


def test_hydrate_env_fills_the_names_the_code_reads(ssm, monkeypatch, caplog) -> None:
    for env in RUNTIME_SECRETS:
        monkeypatch.delenv(env, raising=False)
    ssm.put_parameter(Name="/doorstep/telegram/bot_token", Value=FAKE_TOKEN, Type="SecureString")
    ssm.put_parameter(Name="/doorstep/telegram/captain_chat_id", Value="4242", Type="SecureString")

    configure_logging()
    caplog.set_level(logging.INFO)
    missing = hydrate_env(client=ssm)

    import os

    assert os.environ["TELEGRAM_BOT_TOKEN"] == FAKE_TOKEN
    assert os.environ["TELEGRAM_CAPTAIN_CHAT_ID"] == "4242"
    assert sorted(missing) == ["CALL_ALLOWLIST", "TELEGRAM_VOLUNTEER_CHAT_IDS"]
    assert FAKE_TOKEN not in caplog.text and "4242" not in caplog.text


def test_the_kill_switch_fails_closed(ssm) -> None:
    flags = SsmFlags(client=ssm, ttl=0)
    assert flags.kill_switch() is True, "an unreadable switch must refuse spending"
    ssm.put_parameter(Name="/doorstep/kill_switch", Value="off", Type="String")
    assert flags.kill_switch() is False
    ssm.put_parameter(Name="/doorstep/kill_switch", Value="on", Type="String", Overwrite=True)
    assert flags.kill_switch() is True


def test_the_bot_token_never_reaches_the_logs(caplog) -> None:
    """httpx logs every request URL at INFO, and the token is in the URL path."""
    configure_logging()
    caplog.set_level(logging.INFO)
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="nope"))
    bot = Bot(FAKE_TOKEN)
    bot._http = httpx.Client(transport=transport)
    with pytest.raises(TelegramError) as raised:
        bot.call("sendMessage", chat_id=1, text="hi")
    assert FAKE_TOKEN not in str(raised.value)
    assert FAKE_TOKEN not in caplog.text
    assert logging.getLogger("httpx").level == logging.WARNING


def test_cloud_mode_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("DOORSTEP_ENV", raising=False)
    assert running_in_aws() is False
    monkeypatch.setenv("DOORSTEP_ENV", "cloud")
    assert running_in_aws() is True
