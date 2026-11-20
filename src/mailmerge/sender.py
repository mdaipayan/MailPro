"""SMTP sending. Kept separate so the core can be tested without a network."""

from __future__ import annotations

import configparser
import getpass
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional, Union

from .core import MergeError

PASSWORD_ENV = "MAILMERGE_PASSWORD"

# Servers that accept mail on two ports. If the first is closed, try the other.
OTHER_DOORS = {
    "smtp.gmail.com": [(587, "starttls"), (465, "ssl")],
}


@dataclass
class ServerConfig:
    host: str
    port: int
    security: str = "starttls"  # starttls | ssl | none
    username: Optional[str] = None

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "ServerConfig":
        path = Path(path)
        if not path.exists():
            raise MergeError(f"Server config not found: {path}")
        cp = configparser.ConfigParser()
        cp.read(path, encoding="utf-8")
        if "smtp_server" not in cp:
            raise MergeError(f"{path} needs an [smtp_server] section.")
        s = cp["smtp_server"]
        security = s.get("security", "starttls").strip().lower()
        if security not in ("starttls", "ssl", "none"):
            raise MergeError("security must be starttls, ssl or none.")
        try:
            port = s.getint("port", 587 if security != "ssl" else 465)
        except ValueError as exc:
            raise MergeError("port must be a number.") from exc
        host = s.get("host", "").strip()
        if not host:
            raise MergeError("[smtp_server] needs a host.")
        return cls(host=host, port=port, security=security,
                   username=s.get("username", "").strip() or None)


class Sender:
    """Open one SMTP connection and reuse it for every message.

    allow_prompt=False (used by the app) never asks on the terminal;
    the password must then be given or be in MAILMERGE_PASSWORD.
    """

    def __init__(self, config: ServerConfig, password: Optional[str] = None,
                 allow_prompt: bool = True):
        self.config = config
        self.password = password
        self.allow_prompt = allow_prompt
        self._smtp: Optional[smtplib.SMTP] = None

    def _get_password(self) -> str:
        if not self.password:
            self.password = os.environ.get(PASSWORD_ENV)
        if not self.password:
            if not self.allow_prompt:
                raise MergeError("The password is missing.")
            self.password = getpass.getpass(f"SMTP password for {self.config.username}: ")
        return self.password

    def _open(self, c: ServerConfig) -> None:
        """One try with one port. Raises the plain smtplib / OS error."""
        ctx = ssl.create_default_context()
        if c.security == "ssl":
            self._smtp = smtplib.SMTP_SSL(c.host, c.port, context=ctx, timeout=30)
        else:
            self._smtp = smtplib.SMTP(c.host, c.port, timeout=30)
            if c.security == "starttls":
                self._smtp.starttls(context=ctx)
        if c.username:
            password = self._get_password()
            if c.host.lower() in OTHER_DOORS:          # Gmail shows App Passwords as
                password = "".join(password.split())  # "abcd efgh ijkl mnop"
            self._smtp.login(c.username, password)

    def _connect(self) -> None:
        """Connect and log in.

        Some networks and cloud hosts close one mail port but not the other.
        For a server known to offer both, the second door is tried
        automatically, and the one that works is kept for the next emails.
        """
        first = self.config
        tries = [first] + [
            ServerConfig(first.host, port, security, first.username)
            for port, security in OTHER_DOORS.get(first.host.lower(), [])
            if port != first.port
        ]
        failures = []
        for c in tries:
            try:
                self._open(c)
                self.config = c
                return
            except smtplib.SMTPAuthenticationError as exc:
                raise MergeError(
                    "Login failed. For Gmail, use an App Password, not your normal password."
                ) from exc
            except (OSError, smtplib.SMTPException) as exc:
                failures.append((c.port, str(exc) or exc.__class__.__name__))
        if len(failures) == 1:
            port, why = failures[0]
            raise MergeError(f"Cannot connect to {first.host}:{port}: {why}")
        ports = " or ".join(str(p) for p, _ in failures)
        raise MergeError(
            f"Cannot connect to {first.host} on port {ports}: {failures[-1][1]}. "
            "This network or host seems to block outgoing email."
        )

    def __enter__(self) -> "Sender":
        self._connect()
        return self

    def send(self, msg: EmailMessage, recipients: List[str]) -> None:
        assert self._smtp is not None, "use 'with Sender(...) as s:'"
        # If the server dropped an idle connection, open a new one first.
        # (Checked BEFORE sending, so no email can be sent twice.)
        try:
            alive = self._smtp.noop()[0] == 250
        except (OSError, smtplib.SMTPException):
            alive = False
        if not alive:
            self._connect()
        self._smtp.send_message(msg, to_addrs=recipients)

    def __exit__(self, *exc) -> None:
        if self._smtp is not None:
            try:
                self._smtp.quit()
            except (OSError, smtplib.SMTPException):
                pass
            self._smtp = None
