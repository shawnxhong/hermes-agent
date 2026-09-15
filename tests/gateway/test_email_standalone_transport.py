"""Standalone SMTP transport must be bounded and safe to retry."""

import asyncio
import socket
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


def test_voice_scoped_connect_timeout_does_not_change_default(smtp):
    _, _, plain, _ = smtp
    with adapter.smtp_connect_timeout(8):
        assert send()["success"]
    assert plain.call_args.kwargs["timeout"] == 8
    plain.reset_mock()
    assert send()["success"]
    assert plain.call_args.kwargs["timeout"] == adapter.SMTP_CONNECT_TIMEOUT


def test_shared_timeout_survives_duplicate_plugin_module(smtp, monkeypatch):
    import importlib.util
    from hermes_cli.email_transport import smtp_connect_timeout
    spec = importlib.util.spec_from_file_location('email_plugin_alias_under_test', adapter.__file__)
    duplicate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(duplicate)
    monkeypatch.setattr(duplicate, '_get_secret', adapter._get_secret)
    monkeypatch.setattr(duplicate, '_esecret_bool', adapter._esecret_bool)
    with smtp_connect_timeout(8):
        assert asyncio.run(duplicate._standalone_send(SimpleNamespace(extra={}),
                          'recipient@example.com', 'Test report.'))['success']
    assert smtp[2].call_args.kwargs['timeout'] == 8
    assert duplicate._standalone_smtp_connect_timeout() == 30


def test_connect_failure_is_definite_and_structured(smtp):
    _, _, plain, _ = smtp
    plain.side_effect = socket.gaierror(-2, "Name or service not known")
    result = send()
    assert result["error_code"] == "transport_unreachable"
    assert result["delivery_stage"] == "connect"
    assert result["definitive_not_accepted"] is True


def test_failed_send_releases_connection(smtp):
    _, server, _, _ = smtp
    server.send_message.side_effect = smtplib.SMTPDataError(550, b"rejected")
    result=send()
    assert "error" in result
    assert result["error_code"] == "ambiguous_delivery"
    assert result["delivery_stage"] == "data"
    server.quit.assert_called_once()


def test_quit_failure_does_not_report_accepted_message_as_failed(smtp):
    _, server, _, _ = smtp
    server.quit.side_effect = smtplib.SMTPServerDisconnected("closed after DATA")
    assert send()["success"]
    server.send_message.assert_called_once()
    server.close.assert_called_once()
