"""Quotas: what the AI extracts from a page, and how two extractions are compared.

Everything here is pure, so the test suite exercises it without a Workers runtime.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

# JSON mode for Workers AI: the model has to return an object matching this schema.
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "quotas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "metric": {"type": "string"},
                    "limit": {"type": "string"},
                    "period": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["service", "metric", "limit", "period"],
            },
        }
    },
    "required": ["quotas"],
}

SYSTEM_PROMPT = """\
You extract free-tier quotas from cloud provider documentation.

Return every limit that applies to the FREE plan or free tier, and nothing that applies
only to paid plans. One entry per limit:
- service: the product name, e.g. "Workers KV"
- metric: what is limited, e.g. "keys written"
- limit: the number with its unit exactly as written, e.g. "1,000" or "10 GB", without
  the period: "10 million requests", not "10 million requests / month"
- period: "per day", "per month", "per request", "total" or "per account"
- notes: anything a reader needs to interpret the limit, or ""

Be exhaustive. A pricing page usually lists several services, each with its own free
allowance: go through the whole page and include every service, not just the first few.

Copy numbers exactly as the page writes them. Never estimate, round or convert. Skip any
limit the page doesn't give a number for. If the page lists no free-plan limits, return
an empty list."""


@dataclass(frozen=True)
class Quota:
    service: str
    metric: str
    limit: str
    period: str
    notes: str = ""

    @property
    def key(self) -> str:
        return quota_key(self.service, self.metric)

    @property
    def value(self) -> str:
        """The part of a quota whose change is worth reporting."""
        return f"{self.limit} {self.period}".strip()

    @property
    def comparable(self) -> str:
        """`value` with formatting the model varies between runs taken out.

        Measured on one page across two runs: "10 million requests" + "per month" became
        "10 million requests / month" + "per month". Same limit, so it mustn't read as a
        change. The period folded into the limit is dropped and spacing and case are
        normalized; the digits and units, which are the part that matters, are untouched.
        """
        limit = _PERIOD_IN_LIMIT.sub("", self.limit.lower())
        period = self.period.lower().removeprefix("per ").strip()
        return re.sub(r"\s+", " ", f"{limit} | {period}").strip()


_PERIOD_IN_LIMIT = re.compile(r"\s*(/|per|a|each)\s*(day|month|year|request|invocation|account)s?\b")


def quota_key(service: str, metric: str) -> str:
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

    return f"{norm(service)}|{norm(metric)}"


def build_messages(provider_name: str, page_title: str, text: str, known: list["Quota"] | None = None) -> list[dict]:
    """`known` is what the last check recorded for this page. Without it the model renames
    things between runs ("log retention" one week, "retention" the next), and every
    rename reads as one quota removed and another added."""
    header = f"Provider: {provider_name}\nPage: {page_title}"
    if known:
        names = "\n".join(f"- {q.service} | {q.metric}" for q in known)
        header += (
            "\n\nNames already recorded for this page, as service | metric. When the page "
            f"describes one of these limits, use exactly this service and metric:\n{names}"
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{header}\n\n{text}"},
    ]


def merge(*extractions: list["Quota"]) -> list["Quota"]:
    """Combine per-chunk extractions, keeping the first occurrence of each quota."""
    out: dict[str, Quota] = {}
    for quotas in extractions:
        for q in quotas:
            out.setdefault(q.key, q)
    return list(out.values())


def parse_extraction(result) -> list[Quota]:
    """Read a Workers AI JSON-mode result into quotas.

    Depending on the model, `response` arrives as an already-parsed object or as a JSON
    string, sometimes wrapped in a markdown fence. Rows missing a required field are
    dropped, and duplicates (same service and metric) keep the first occurrence.
    """
    payload = result.get("response", result) if isinstance(result, dict) else result
    if isinstance(payload, str):
        payload = json.loads(_strip_fence(payload))
    rows = payload.get("quotas", []) if isinstance(payload, dict) else []

    quotas: dict[str, Quota] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        fields = {k: str(row.get(k) or "").strip() for k in ("service", "metric", "limit", "period", "notes")}
        if not all(fields[k] for k in ("service", "metric", "limit")):
            continue
        quota = Quota(**fields)
        quotas.setdefault(quota.key, quota)
    return list(quotas.values())


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    return s.strip()


@dataclass
class Changes:
    added: list[Quota] = field(default_factory=list)
    removed: list[Quota] = field(default_factory=list)
    changed: list[tuple[Quota, Quota]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def count(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)

    def rows(self) -> list[dict]:
        """Flat rows for the `changes` table and the live feed."""
        out = [{"kind": "added", "service": q.service, "metric": q.metric, "old": None, "new": q.value} for q in self.added]
        out += [{"kind": "removed", "service": q.service, "metric": q.metric, "old": q.value, "new": None} for q in self.removed]
        out += [
            {"kind": "changed", "service": new.service, "metric": new.metric, "old": old.value, "new": new.value}
            for old, new in self.changed
        ]
        return out


def diff(old: list[Quota], new: list[Quota]) -> Changes:
    """Compare two extractions of the same page.

    A notes-only edit isn't a change: notes are prose and the model rewords them between
    runs. Only the limit and its period count.
    """
    before = {q.key: q for q in old}
    after = {q.key: q for q in new}
    changes = Changes()
    for key, q in after.items():
        if key not in before:
            changes.added.append(q)
        elif before[key].comparable != q.comparable:
            changes.changed.append((before[key], q))
    changes.removed = [q for key, q in before.items() if key not in after]
    return changes


def quota_text(provider_name: str, q: Quota) -> str:
    """The sentence embedded for semantic search."""
    text = f"{provider_name} {q.service}: {q.metric} {q.limit} {q.period}".strip()
    return f"{text}. {q.notes}" if q.notes else text


def vector_id(provider_id: str, q: Quota) -> str:
    # Vectorize ids are capped at 64 bytes; a hash keeps long service names inside it.
    return hashlib.sha1(f"{provider_id}|{q.key}".encode()).hexdigest()
