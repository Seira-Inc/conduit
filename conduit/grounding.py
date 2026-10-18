"""Reject write requests to paths the application itself was never seen using.

A recalled vendor endpoint looks plausible and is discovered to be wrong only by sending it at a real
account, so a write target must appear in first-party evidence. Vendor documentation is excluded:
that is where the recalled paths live.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .models import origin
from .programs import read_request, walk

SELF_PHASES = ("verify:", "runtime")
# A record identifier, not a route word. Length alone is not enough: "chirp-frontend-app" and
# "com.hubspot.crm.objects.rpc.CrmObjectReadRpc" are long but name an API, so collapsing them
# would erase the namespace a later write has to be matched against.
VARIABLE = re.compile(r"""^(
      \d+                                           # 4821
    | [0-9a-f]{8,}                                  # 9f86d081884c7d65
    | [0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}     # a UUID with dashes
    | (?=[\w~-]*\d)(?=[\w~-]*[a-z])[\w~-]{12,}      # a long mixed letter/digit token
)$""", re.I | re.X)


def shape(path: str) -> str:
    """A path with identifier-looking segments collapsed, so one observation covers every record."""
    parts = [part for part in path.split("/") if part]
    return "/" + "/".join("*" if VARIABLE.match(part) else part for part in parts)


def target(url: str) -> tuple[str, str]:
    split = urlsplit(url)
    return origin(url), shape(split.path)


def observed(events, origins) -> set[tuple[str, str]]:
    """Request targets the application's own pages issued."""
    seen = set()
    for event in events:
        if event.get("kind") != "http" or not event.get("url"):
            continue
        initiator = event.get("initiator")
        # Requests this adapter issued itself carry no initiator, and verification/runtime traffic is
        # our own doing; neither may ground a later write.
        if not initiator or origin(initiator) not in origins:
            continue
        if str(event.get("phase", "")).startswith(SELF_PHASES):
            continue
        seen.add(target(event["url"]))
    return seen


def first_party_text(collector) -> str:
    """The site's own delivered documents. Vendor documentation is excluded on purpose."""
    return "\n".join(collector.documents.get(source["url"], "")
                     for source in collector.sources if source.get("kind") != "documentation")


class Ledger:
    def __init__(self, events, origins, text=""):
        self.targets = observed(events, origins)
        self.text = text

    def grounded(self, url: str) -> bool:
        where, path = target(url)
        if (where, path) in self.targets:
            return True
        # Ordinary REST moves between a collection and its members in both directions: seeing the
        # list grounds a write to one record, and seeing a record grounds creating into the list.
        if (where, path.rsplit("/", 1)[0] or "/") in self.targets:
            return True
        if (where, path.rstrip("/") + "/*") in self.targets:
            return True
        if self.shares_namespace(where, path):
            return True
        return bool(urlsplit(url).path) and urlsplit(url).path in self.text

    def shares_namespace(self, where: str, path: str) -> bool:
        """Accept an operation inside an API namespace the site was observed using.

        An RPC gateway puts the service and method in the path, so a write never matches a read
        exactly even though both are plainly the app's own API. Two identical leading segments are
        required and a collapsed `*` never counts, so a different namespace still fails: a guessed
        `/contacts/v1/contact` shares only `contacts` with an observed `/contacts/*/objects/...`.
        """
        wanted = [part for part in path.split("/") if part]
        for seen_where, seen_path in self.targets:
            if seen_where != where:
                continue
            seen = [part for part in seen_path.split("/") if part]
            shared = 0
            for mine, theirs in zip(wanted, seen, strict=False):
                if mine != theirs or mine == "*":
                    break
                shared += 1
            if shared >= 2 and shared < len(seen):
                return True
        return False

    def reject(self, method: str, url: str) -> str | None:
        if self.grounded(url):
            return None
        return (f"Write requests must target a path this site's own frontend was observed using; "
                f"{method} {url} was never observed. Recalled vendor API knowledge is not evidence. "
                f"Use probe_record to watch the application perform the write once and replay what "
                f"you observe, or implement the write with real UI steps.")


def write_targets(program):
    """Literal URLs of the program's mutating request steps."""
    for step in walk(program.steps):
        if step.kind != "request" or read_request(step):
            continue
        if isinstance(step.url, str) and step.url.startswith(("http://", "https://")):
            yield step.method, step.url
