"""The pure half of search: keyword matching and turning reranker output into results.

Search is two layers. A query whose words all appear in a quota's provider, service and
metric is a lookup ("Workers AI", "D1 storage"), and it returns exactly those quotas. Any
other query is a question ("how many database writes a day?"). Vectorize finds the 20
nearest quotas, and a model picks the ones that actually answer it.

Each simpler version failed on a measured case:

- Plain vector search returned the 10 nearest quotas whatever the query, so "Workers AI"
  listed its one quota followed by nine unrelated Workers limits, all scored between
  0.63 and 0.74. Cosine scores sit in that band whether or not a row is relevant.
- A reranker (bge-reranker-base) was sharp when the question used the page's own words,
  0.998 for "how long are queue messages kept", and blind when it didn't: R2 scored
  0.001 for "free object storage", below Durable Objects, and the one Workers AI quota
  scored 0.003 for "how much AI inference is free". It doesn't know R2 *is* object storage.

In every case the right answer was among Vectorize's 20 candidates. What was missing was
judgment about the domain, which is what the language model supplies.
"""

import re

_WORD = re.compile(r"[a-z0-9]+")
_QUOTED = re.compile(r'"([^"]+)"')


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def haystack(row: dict) -> str:
    return f"{row.get('provider', '')} {row['service']} {row['metric']}"


def keyword_matches(query: str, rows: list[dict]) -> list[dict]:
    """Rows where every query word appears as a whole word, and every quoted phrase
    appears as consecutive words. Whole words, so "ai" doesn't match "Cloudflare"."""
    phrases = [words(p) for p in _QUOTED.findall(query)]
    terms = words(_QUOTED.sub(" ", query)) + [w for p in phrases for w in p]
    if not terms:
        return []
    out = []
    for row in rows:
        hay = words(haystack(row))
        if not set(terms) <= set(hay):
            continue
        if all(_contains(hay, p) for p in phrases if p):
            out.append(row)
    return out


def _contains(seq: list[str], sub: list[str]) -> bool:
    n = len(sub)
    return any(seq[i : i + n] == sub for i in range(len(seq) - n + 1))


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"ids": {"type": "array", "items": {"type": "integer"}}},
    "required": ["ids"],
}

JUDGE_PROMPT = """\
You match a question about cloud free tiers to the limits that answer it.

You get the question and a numbered list of candidate limits. Return the numbers of the
candidates that directly answer the question, best answer first. Use what you know about
the products: R2 is object storage, D1 is a SQL database, Browser Rendering runs headless
browsers. Leave out candidates that are merely related. If none answers it, return an
empty list."""


def candidate_line(c: dict) -> str:
    text = f"{c.get('provider', '')} {c.get('service', '')}: {c.get('metric', '')} is {c.get('limit', '')} {c.get('period', '')} (from {c.get('page', '')})"
    notes = c.get("notes")
    return f"{text}. {notes}" if notes else text


def build_judge_messages(question: str, candidates: list[dict]) -> list[dict]:
    listing = "\n".join(f"[{i}] {candidate_line(c)}" for i, c in enumerate(candidates))
    return [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user", "content": f"Question: {question}\n\nCandidates:\n{listing}"},
    ]


def parse_judge(result, count: int) -> list[int]:
    """Valid, de-duplicated candidate indexes in the model's order."""
    payload = result.get("response", result) if isinstance(result, dict) else result
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload.strip().strip("`").removeprefix("json").strip())
        except ValueError:
            return []
    ids = payload.get("ids", []) if isinstance(payload, dict) else []
    out = []
    for i in ids:
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if 0 <= i < count and i not in out:
            out.append(i)
    return out
