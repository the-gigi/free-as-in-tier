"""Turning a fetched page into stable text, and hashing it.

The hash decides whether a page reaches the AI step at all, so it has to ignore edits
that never change a limit. Cloudflare's markdown pages carry a "Last updated" stamp and
navigation boilerplate that move on their own; both are dropped before hashing.
"""

import hashlib
import re

# Lines that change without the page's content changing.
_NOISE = (
    re.compile(r"^Last updated .*$", re.MULTILINE),
    re.compile(r"^\[Skip to content\]\(.*\)$", re.MULTILINE),
    # Cloudflare's "Documentation Index" banner. Only those lines: other blockquotes are
    # notes, and some of them carry free-plan limits.
    re.compile(r"^> (Documentation Index|Fetch the complete documentation index|Use this file to discover).*$", re.MULTILINE),
    re.compile(r"^image: .*$", re.MULTILINE),  # front-matter image URL carries a cache-busting version
)
_BLANK_RUNS = re.compile(r"\n{3,}")

# Workers AI models have a context window, and a page longer than this is mostly
# changelog and examples by the time it gets there.
MAX_CHARS = 60_000

# Measured: given the whole 35 KB Workers pricing page in one call, the model extracted
# the sections before and after Queues and Workflows and skipped those two. Pages are
# split at their `## ` headings into chunks no bigger than this, one call each.
CHUNK_CHARS = 12_000


def normalize(raw: str) -> str:
    text = raw.replace("\r\n", "\n")
    for pattern in _NOISE:
        text = pattern.sub("", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_RUNS.sub("\n\n", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clip(text: str, limit: int = MAX_CHARS) -> str:
    return text if len(text) <= limit else text[:limit]


def chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split at `## ` headings, then pack whole sections into chunks of at most `limit`
    characters. A single section longer than that is cut at the limit."""
    sections, current = [], []
    for line in text.split("\n"):
        if line.startswith("## ") and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current))

    out, buf = [], ""
    for section in sections:
        while len(section) > limit:
            if buf:
                out.append(buf)
                buf = ""
            out.append(section[:limit])
            section = section[limit:]
        if buf and len(buf) + 1 + len(section) > limit:
            out.append(buf)
            buf = section
        else:
            buf = f"{buf}\n{section}" if buf else section
    if buf.strip():
        out.append(buf)
    return [c for c in out if c.strip()]
