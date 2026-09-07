"""Standalone SMTP transport must be bounded and safe to retry."""

import asyncio
import smtplib
import ssl
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from plugins.platforms.email import adapter


@pytest.fixture
def smtp(monkeypatch):
    values = {
        "EMAIL_ADDRESS": "sender@example.com",
        "EMAIL_PASSWORD": "test-token",
        "EMAIL_SMTP_HOST": "smtp.example.com",
    }
    monkeypatch.setattr(adapter, "_get_secret", lambda key, default="": values.get(key, default))
    monkeypatch.setattr(adapter, "_esecret_bool", lambda key, default=False: default)
    server = MagicMock()
    plain = MagicMock(return_value=server)
    tls = MagicMock(return_value=server)
    monkeypatch.setattr(smtplib, "SMTP", plain)
    monkeypatch.setattr(smtplib, "SMTP_SSL", tls)
    return values, server, plain, tls


def send():
    return asyncio.run(adapter._standalone_send(
        SimpleNamespace(extra={}), "recipient@example.com", "功能验证"
    ))


@pytest.mark.parametrize("port", ["465", "587"])
def test_verified_transport_and_timeout(smtp, port):
    values, server, plain, tls = smtp
    values["EMAIL_SMTP_PORT"] = port
    assert send()["success"]
    factory = tls if port == "465" else plain
    assert factory.call_args.kwargs["timeout"] == adapter.SMTP_CONNECT_TIMEOUT
    if port == "465":
        plain.assert_not_called()
        server.starttls.assert_not_called()
        context = tls.call_args.kwargs["context"]
    else:
        tls.assert_not_called()
        context = server.starttls.call_args.kwargs["context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    server.login.assert_called_once_with("sender@example.com", "test-token")
    server.send_message.assert_called_once()
    server.quit.assert_called_once()


def test_failed_send_releases_connection(smtp):
    _, server, _, _ = smtp
    server.send_message.side_effect = smtplib.SMTPDataError(550, b"rejected")
    assert "error" in send()
    server.quit.assert_called_once()


def test_quit_failure_does_not_report_accepted_message_as_failed(smtp):
    _, server, _, _ = smtp
    server.quit.side_effect = smtplib.SMTPServerDisconnected("closed after DATA")
    assert send()["success"]
    server.send_message.assert_called_once()
    server.close.assert_called_once()
