"""Session storage isolated per connector; Windows encrypts it to the signed-in user."""

import json
import os
from pathlib import Path


def save_session(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state).encode("utf-8")
    if os.name == "nt":
        import win32crypt

        payload = b"DPAPI\n" + win32crypt.CryptProtectData(
            payload, "Conduit browser session", None, None, None, 0
        )
    else:
        payload = b"JSON\n" + payload
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(payload)
    if os.name != "nt":
        temporary.chmod(0o600)
    temporary.replace(path)


def load_session(path: Path) -> dict | None:
    if not path.exists():
        return None
    mode, payload = path.read_bytes().split(b"\n", 1)
    if mode == b"DPAPI":
        if os.name != "nt":
            raise ValueError("This session belongs to a Windows user. Sign in again on this computer.")
        import win32crypt

        payload = win32crypt.CryptUnprotectData(payload, None, None, None, 0)[1]
    return json.loads(payload)
