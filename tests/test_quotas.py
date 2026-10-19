import json

from tracker.quotas import (
    Quota,
    diff,
    parse_extraction,
    quota_key,
    vector_id,
)

KV_WRITES = Quota("Workers KV", "keys written", "1,000", "per day")
KV_READS = Quota("Workers KV", "keys read", "100,000", "per day")
D1_STORAGE = Quota("D1", "storage", "5 GB", "total")


def test_parse_object_response():
    result = {"response": {"quotas": [{"service": "Workers KV", "metric": "keys written", "limit": "1,000", "period": "per day"}]}}
    assert parse_extraction(result) == [KV_WRITES]


def test_parse_string_response_in_a_fence():
    body = json.dumps({"quotas": [{"service": "D1", "metric": "storage", "limit": "5 GB", "period": "total", "notes": None}]})
    assert parse_extraction({"response": f"```json\n{body}\n```"}) == [D1_STORAGE]


def test_parse_drops_incomplete_rows_and_duplicates():
    rows = [
        {"service": "Workers KV", "metric": "keys written", "limit": "1,000", "period": "per day"},
        {"service": "workers kv", "metric": "Keys Written", "limit": "2,000", "period": "per day"},  # same key
        {"service": "D1", "metric": "", "limit": "5 GB", "period": "total"},  # no metric
        "not a row",
    ]
    assert parse_extraction({"response": {"quotas": rows}}) == [KV_WRITES]


def test_parse_empty():
    assert parse_extraction({"response": {"quotas": []}}) == []
    assert parse_extraction({"response": {}}) == []


def test_key_normalizes_case_and_punctuation():
    assert quota_key("Workers KV", "keys written") == quota_key("workers-kv", "Keys  Written!")


def test_diff_finds_each_kind_of_change():
    cut = Quota("Workers KV", "keys read", "50,000", "per day")
    changes = diff([KV_WRITES, KV_READS], [cut, D1_STORAGE])
    assert changes.added == [D1_STORAGE]
    assert changes.removed == [KV_WRITES]
    assert changes.changed == [(KV_READS, cut)]
    assert changes.count() == 3
    assert {r["kind"] for r in changes.rows()} == {"added", "removed", "changed"}


def test_notes_only_edit_is_not_a_change():
    reworded = Quota("Workers KV", "keys written", "1,000", "per day", "resets at midnight UTC")
    assert not diff([KV_WRITES], [reworded])


def test_first_extraction_is_all_added():
    changes = diff([], [KV_WRITES, D1_STORAGE])
    assert changes.added == [KV_WRITES, D1_STORAGE] and not changes.removed and not changes.changed


def test_vector_id_is_stable_and_short():
    assert vector_id("cloudflare", KV_WRITES) == vector_id("cloudflare", Quota("workers kv", "Keys written", "9", "x"))
    assert len(vector_id("cloudflare", KV_WRITES).encode()) <= 64


def test_build_messages_lists_known_names():
    from tracker.quotas import build_messages

    user = build_messages("Cloudflare", "Workers pricing", "page text", [KV_WRITES])[1]["content"]
    assert "- Workers KV | keys written" in user and user.endswith("page text")
    assert "already recorded" not in build_messages("Cloudflare", "Workers pricing", "page text")[1]["content"]


def test_merge_keeps_first_occurrence():
    from tracker.quotas import merge

    later = Quota("workers kv", "Keys Written", "2,000", "per day")
    assert merge([KV_WRITES], [later, D1_STORAGE]) == [KV_WRITES, D1_STORAGE]


def test_period_folded_into_the_limit_is_not_a_change():
    before = Quota("R2", "Class B operations", "10 million requests", "per month")
    after = Quota("R2", "Class B operations", "10 million requests / month", "per month")
    assert not diff([before], [after])


def test_a_real_cut_still_counts():
    before = Quota("R2", "storage", "10 GB-month", "per month")
    after = Quota("R2", "storage", "5 GB-month / month", "per month")
    assert diff([before], [after]).changed == [(before, after)]
