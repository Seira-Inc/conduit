"""Append-only record of every tool call through the gateway: who, which tool, how it was approved,
how it ended. Arguments are hashed, never stored, and receipts stay out of it."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


def argument_hash(arguments) -> str:
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


class Audit:
    def __init__(self, path: Path):
        self.path = path

    def record(self, **fields) -> dict:
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def tail(self, count: int = 50) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows = []
        for line in lines[-count:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows
