from __future__ import annotations

from rss_to_kindle.mailer import MailConfigError, _smtp_settings


def test_smtp_settings_require_kindle_and_sender_env(monkeypatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.delenv("KINDLE_EMAIL", raising=False)
    monkeypatch.delenv("SENDER_EMAIL", raising=False)

    try:
        _smtp_settings()
    except MailConfigError as exc:
        assert "kindle_email" in str(exc)
        assert "sender_email" in str(exc)
    else:
        raise AssertionError("Expected MailConfigError")


def test_smtp_settings_read_delivery_addresses_from_env(monkeypatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "user@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    monkeypatch.setenv("KINDLE_EMAIL", "kindle@example.com")
    monkeypatch.setenv("SENDER_EMAIL", "sender@example.com")

    settings = _smtp_settings()

    assert settings["kindle_email"] == "kindle@example.com"
    assert settings["sender_email"] == "sender@example.com"
    assert settings["use_tls"] is False
