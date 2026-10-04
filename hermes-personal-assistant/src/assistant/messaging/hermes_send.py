"""Sends a text the user didn't just ask for (reminders) through the running Hermes gateway.

Uses `hermes send`, which needs no model call. Shared Photon lines can only message people who have
texted the bot before, which every signed-up user has.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger(__name__)

E164 = re.compile(r"^\+\d{8,15}$")
E164_ANYWHERE = re.compile(r"\+\d{8,15}")
# `hermes send` treats MEDIA:<path> as "attach this local file", so message text must never contain it.
MEDIA_DIRECTIVE = re.compile(r"media\s*:", re.IGNORECASE)
MAX_MESSAGE_LENGTH = 1000
SEND_TIMEOUT_SECONDS = 90


class MessagingError(Exception):
    pass


class MessageSender(Protocol):
    def send(self, phone: str, text: str) -> None: ...


def safe_text(text: str) -> str:
    cleaned = MEDIA_DIRECTIVE.sub("media ", text).replace("[[", "[ [")
    return cleaned[:MAX_MESSAGE_LENGTH]


Runner = Callable[..., subprocess.CompletedProcess]


class HermesSender:
    def __init__(self, hermes_cmd: Path, runner: Runner = subprocess.run):
        self._hermes_cmd = hermes_cmd
        self._run = runner

    def send(self, phone: str, text: str) -> None:
        if not E164.match(phone or ""):
            raise MessagingError("Refusing to send to a malformed phone number.")
        # The body goes through a UTF-8 file, never the command line: hermes.cmd is a batch file, and cmd.exe
        # would interpret characters like & and | in calendar titles.
        fd, path = tempfile.mkstemp(prefix="assistant-msg-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(safe_text(text))
            try:
                result = self._run(
                    [str(self._hermes_cmd), "send", "--to", f"photon:any;-;{phone}", "--file", path, "--json"],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=SEND_TIMEOUT_SECONDS, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.warning("hermes send failed to run: %s", exc)
                raise MessagingError("Couldn't run hermes send.") from exc
        finally:
            Path(path).unlink(missing_ok=True)

        try:
            payload = json.loads(result.stdout or "{}")
        except ValueError:
            payload = {}
        if result.returncode != 0 or not payload.get("success"):
            log.warning("hermes send failed (exit %s): %s | stdout: %s | stderr: %s", result.returncode,
                        str(payload.get("error") or "")[:300], (result.stdout or "")[-500:],
                        E164_ANYWHERE.sub("<number>", (result.stderr or "")[-1500:]))
            raise MessagingError("hermes send reported a failure.")
