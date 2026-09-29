"""E-mail provider abstraction.

    EmailProvider.send_email()        send one approved message
    EmailProvider.get_inbox()         recent incoming messages
    EmailProvider.get_message(id)     one incoming message by Message-ID
    EmailProvider.reply_to_message()  reply in the same thread (In-Reply-To / References)

Two adapters:

- LIVE  (ER_EMAIL_PROVIDER=smtp): SMTP for sending, IMAP (read-only) for the inbox. Credentials only from the
  environment; nothing is invented. If the provider is selected but not configured, every call raises
  EmailNotConfigured: it never silently falls back to the mock.
- MOCK  (ER_EMAIL_PROVIDER=mock, the default): nothing leaves this computer. Sent messages are written as .eml
  files to <data_dir>/mock_mail/sent; the inbox is <data_dir>/mock_mail/inbox, where the UI can drop a
  simulated reply. Everything the mock touches is labelled MOCK in the UI.
"""

from __future__ import annotations

import email
import imaplib
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class EmailError(Exception):
    """Sending or reading failed; the message says why (shown in the UI)."""


class EmailNotConfigured(EmailError):
    pass


@dataclass
class SentEmail:
    message_id: str
    live: bool  # True only when a real mail server accepted the message
    reference: str  # Message-ID (live) or file name (mock)


@dataclass
class IncomingMail:
    external_id: str
    from_email: str
    from_name: str | None
    subject: str
    body: str
    received_at: datetime
    in_reply_to: str | None


class EmailProvider(Protocol):
    id: str
    label: str
    is_live: bool

    def send_email(self, *, to_email: str, to_name: str | None, subject: str, body: str,
                   in_reply_to: str | None = None) -> SentEmail: ...

    def get_inbox(self, limit: int = 50) -> list[IncomingMail]: ...

    def get_message(self, external_id: str) -> IncomingMail | None: ...

    def reply_to_message(self, original: IncomingMail, *, subject: str, body: str) -> SentEmail: ...


# ---------------------------------------------------------------- shared helpers


def sender_address() -> tuple[str, str]:
    s = get_settings()
    return s.outreach_from_name, s.outreach_from_email or s.operator_email


def build_message(to_email: str, to_name: str | None, subject: str, body: str,
                  in_reply_to: str | None = None) -> EmailMessage:
    name, address = sender_address()
    msg = EmailMessage()
    msg["From"] = formataddr((name, address))
    msg["To"] = formataddr((to_name or "", to_email))
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.now(UTC))
    msg["Message-ID"] = make_msgid(domain=address.split("@", 1)[-1])
    msg["List-Unsubscribe"] = f"<mailto:{address}?subject=afmelden>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return msg


def _text(value: str | None) -> str:
    """Decoded header text with folding whitespace collapsed (long subjects arrive as '\\n Re: …')."""
    return " ".join(str(make_header(decode_header(value))).split()) if value else ""


def parse_message(raw: bytes) -> IncomingMail:
    msg = email.message_from_bytes(raw)
    body = ""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_type() == "text/plain" and not part.get_filename():
            payload = part.get_payload(decode=True) or b""
            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            break
    name, address = parseaddr(msg.get("From", ""))
    try:
        received = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else datetime.now(UTC)
    except (TypeError, ValueError):
        received = datetime.now(UTC)
    return IncomingMail(
        external_id=(msg.get("Message-ID") or "").strip()[:300], from_email=address.lower(),
        from_name=_text(name) or None, subject=_text(msg.get("Subject"))[:300] or "(geen onderwerp)",
        body=body[:20000], received_at=received, in_reply_to=(msg.get("In-Reply-To") or "").strip() or None)


def _reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


# ---------------------------------------------------------------- LIVE: SMTP + IMAP


class SmtpImapProvider:
    id = "smtp"
    label = "Mailserver (SMTP/IMAP)"
    is_live = True

    def _require(self, *names: str) -> None:
        s = get_settings()
        missing = [n for n in names if not getattr(s, n)]
        if missing:
            raise EmailNotConfigured("E-mail is niet gekoppeld: stel " + ", ".join(f"ER_{n.upper()}" for n in missing)
                                     + " in.")

    def send_email(self, *, to_email, to_name, subject, body, in_reply_to=None) -> SentEmail:
        self._require("smtp_host")
        s = get_settings()
        msg = build_message(to_email, to_name, subject, body, in_reply_to)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                if s.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                refused = smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError("De mailserver weigerde de inloggegevens (ER_SMTP_USER / ER_SMTP_PASSWORD).") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise EmailError(f"De mailserver weigerde het adres {to_email}.") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailError(f"Mailserver {s.smtp_host}:{s.smtp_port} niet bereikbaar of weigerde het bericht "
                             f"({type(exc).__name__}).") from exc
        if refused:
            raise EmailError(f"De mailserver weigerde {', '.join(refused)}.")
        return SentEmail(message_id=msg["Message-ID"], live=True, reference=msg["Message-ID"])

    def _imap(self):
        self._require("imap_host", "imap_user")
        s = get_settings()
        try:
            imap = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
            imap.login(s.imap_user, s.imap_password or "")
            imap.select(s.imap_folder, readonly=True)
            return imap
        except (OSError, imaplib.IMAP4.error) as exc:
            raise EmailError(f"Mailbox {s.imap_host} niet bereikbaar of inloggen mislukt ({type(exc).__name__}).") \
                from exc

    def get_inbox(self, limit: int = 50) -> list[IncomingMail]:
        imap = self._imap()
        try:
            _, data = imap.search(None, "ALL")
            out = []
            for msg_id in data[0].split()[-limit:]:
                _, parts = imap.fetch(msg_id, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw:
                    out.append(parse_message(raw))
            return out
        except imaplib.IMAP4.error as exc:
            raise EmailError(f"Lezen van de mailbox mislukt ({exc}).") from exc
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: S110 - closing a broken connection
                pass

    def get_message(self, external_id: str) -> IncomingMail | None:
        return next((m for m in self.get_inbox(200) if m.external_id == external_id), None)

    def reply_to_message(self, original: IncomingMail, *, subject: str, body: str) -> SentEmail:
        return self.send_email(to_email=original.from_email, to_name=original.from_name,
                               subject=_reply_subject(subject), body=body, in_reply_to=original.external_id)


# ---------------------------------------------------------------- MOCK: local files only


class MockEmailProvider:
    id = "mock"
    label = "MOCK (niets wordt echt verzonden)"
    is_live = False

    @property
    def root(self) -> Path:
        return get_settings().data_dir / "mock_mail"

    def _dir(self, name: str) -> Path:
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def send_email(self, *, to_email, to_name, subject, body, in_reply_to=None) -> SentEmail:
        msg = build_message(to_email, to_name, subject, body, in_reply_to)
        msg["X-Factuurspoor-Mock"] = "not sent"
        name = msg["Message-ID"].strip("<>").replace("@", "_at_") + ".eml"
        (self._dir("sent") / name).write_bytes(bytes(msg))
        return SentEmail(message_id=msg["Message-ID"], live=False, reference=name)

    def get_inbox(self, limit: int = 50) -> list[IncomingMail]:
        files = sorted(self._dir("inbox").glob("*.eml"), key=lambda f: f.stat().st_mtime)[-limit:]
        return [parse_message(f.read_bytes()) for f in files]

    def get_message(self, external_id: str) -> IncomingMail | None:
        return next((m for m in self.get_inbox(500) if m.external_id == external_id), None)

    def reply_to_message(self, original: IncomingMail, *, subject: str, body: str) -> SentEmail:
        return self.send_email(to_email=original.from_email, to_name=original.from_name,
                               subject=_reply_subject(subject), body=body, in_reply_to=original.external_id)

    def simulate_incoming(self, *, from_email: str, from_name: str | None, subject: str, body: str,
                          in_reply_to: str | None = None) -> IncomingMail:
        """Development only: put a message in the mock inbox, as if someone replied."""
        msg = EmailMessage()
        msg["From"] = formataddr((from_name or "", from_email))
        msg["To"] = formataddr(sender_address())
        msg["Subject"] = subject
        msg["Date"] = format_datetime(datetime.now(UTC))
        msg["Message-ID"] = make_msgid(domain="mock.invalid")
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        msg.set_content(body)
        name = msg["Message-ID"].strip("<>").replace("@", "_at_") + ".eml"
        (self._dir("inbox") / name).write_bytes(bytes(msg))
        return parse_message(bytes(msg))


def get_email_provider(name: str | None = None) -> EmailProvider:
    name = name or get_settings().email_provider
    if name == "smtp":
        return SmtpImapProvider()
    if name == "mock":
        return MockEmailProvider()
    raise ValueError(f"Onbekende e-mailprovider: {name} (kies smtp of mock)")


def provider_status() -> dict:
    """What the UI shows about e-mail: which adapter, and whether sending/reading is possible."""
    s = get_settings()
    p = get_email_provider()
    return {"id": p.id, "label": p.label, "live": p.is_live,
            "can_send": (not p.is_live) or bool(s.smtp_host),
            "can_read": (not p.is_live) or bool(s.imap_host and s.imap_user),
            "from": sender_address()[1]}
