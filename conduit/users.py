"""Who may call the gateway: users.json under the home, one key per person, admin or member, and
blocks on a site, a tool or one record. Without the file the gateway serves one local admin."""

from __future__ import annotations

import hmac
import json
import os
import secrets
from pathlib import Path

LOCAL = {"name": "local", "role": "admin", "blocks": []}


def _matches(block: dict, *, site, tool, record) -> bool:
    return all(block.get(key) in (None, value) for key, value in (("site", site), ("tool", tool), ("record", record)))


class Users:
    def __init__(self, path: Path):
        self.path = path
        self._mtime = None
        self._users: list[dict] = []

    def refresh(self) -> None:
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            self._mtime, self._users = None, []
            return
        if mtime == self._mtime:
            return
        try:
            self._users = list(json.loads(self.path.read_text(encoding="utf-8")).get("users", []))
        except (OSError, ValueError, AttributeError):
            self._users = []
        self._mtime = mtime

    @property
    def enabled(self) -> bool:
        self.refresh()
        return bool(self._users)

    def all(self) -> list[dict]:
        self.refresh()
        return [{k: v for k, v in u.items() if k != "key"} for u in self._users]

    def get(self, name: str) -> dict | None:
        self.refresh()
        return next((u for u in self._users if u["name"] == name), None)

    def by_key(self, key: str | None) -> dict | None:
        self.refresh()
        if not key:
            return None
        for user in self._users:
            if hmac.compare_digest(user.get("key", ""), key):
                return user
        return None

    def add(self, name: str, *, admin: bool = False) -> str:
        if self.get(name) is not None:
            raise ValueError(f"A user named {name!r} already exists")
        key = "sk_" + secrets.token_urlsafe(24)
        self._users.append({"name": name, "role": "admin" if admin else "member", "key": key, "blocks": []})
        self._save()
        return key

    def block(self, name: str, *, site=None, tool=None, record=None) -> list[dict]:
        user = self._known(name)
        rule = {k: v for k, v in (("site", site), ("tool", tool), ("record", record)) if v}
        if not rule:
            raise ValueError("A block names a site, a tool or a record")
        if rule not in user.setdefault("blocks", []):
            user["blocks"].append(rule)
            self._save()
        return user["blocks"]

    def unblock(self, name: str, *, site=None, tool=None, record=None) -> list[dict]:
        user = self._known(name)
        rule = {k: v for k, v in (("site", site), ("tool", tool), ("record", record)) if v}
        user["blocks"] = [b for b in user.get("blocks", []) if b != rule]
        self._save()
        return user["blocks"]

    def _known(self, name: str) -> dict:
        user = self.get(name)
        if user is None:
            raise ValueError(f"No user named {name!r}")
        return user

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"users": self._users}, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            self.path.chmod(0o600)
        self._mtime = self.path.stat().st_mtime_ns


def hidden(user: dict, *, site: str, tool: str) -> bool:
    """A block on the site or the tool (no record named) keeps the tool out of this user's catalog."""
    return any(b.get("record") is None and _matches(b, site=site, tool=tool, record=None)
               for b in user.get("blocks", []))


def refused(user: dict, *, site: str, tool: str, record) -> bool:
    return hidden(user, site=site, tool=tool) or any(
        b.get("record") is not None and _matches(b, site=site, tool=tool, record=record)
        for b in user.get("blocks", []))
