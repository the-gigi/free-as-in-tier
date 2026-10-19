from tracker.text import chunks, clip, content_hash, normalize

PAGE = """---
title: Limits
image: https://developers.cloudflare.com/workers/platform/limits/og.png?v=58be33e8
---

[Skip to content](#main-content)

> Documentation Index
> Fetch the complete documentation index at: https://developers.cloudflare.com/workers/llms.txt

# Limits

Last updated Oct 12, 2026|Copy as Markdown



| Requests | 100,000 per day |

> Free plan: the daily limit resets at midnight UTC.
"""


def test_normalize_drops_noise_that_moves_on_its_own():
    text = normalize(PAGE)
    assert "Last updated" not in text
    assert "Skip to content" not in text
    assert "Documentation Index" not in text
    assert "og.png" not in text
    assert "| Requests | 100,000 per day |" in text
    assert "\n\n\n" not in text
    assert "> Free plan: the daily limit resets at midnight UTC." in text


def test_hash_ignores_the_last_updated_stamp():
    later = PAGE.replace("Oct 12, 2026", "Oct 19, 2026").replace("v=58be33e8", "v=deadbeef")
    assert content_hash(normalize(PAGE)) == content_hash(normalize(later))


def test_hash_sees_a_changed_limit():
    cut = PAGE.replace("100,000", "50,000")
    assert content_hash(normalize(PAGE)) != content_hash(normalize(cut))


def test_clip():
    assert clip("abcdef", 4) == "abcd"
    assert clip("ab", 4) == "ab"


def test_chunks_split_at_sections_and_respect_the_limit():
    text = "# Pricing\nintro\n## Workers\n" + "w" * 50 + "\n## Queues\n" + "q" * 50 + "\n## D1\n" + "d" * 50
    out = chunks(text, limit=80)
    assert all(len(c) <= 80 for c in out)
    assert "".join(out).count("## ") == 3
    assert out[0].startswith("# Pricing")
    assert any(c.startswith("## Queues") for c in out)


def test_chunks_keep_a_short_page_whole():
    assert chunks("## A\nx\n## B\ny", limit=1000) == ["## A\nx\n## B\ny"]


def test_chunks_cut_an_oversized_section():
    out = chunks("## Big\n" + "x" * 250, limit=100)
    assert all(len(c) <= 100 for c in out) and len(out) == 3
