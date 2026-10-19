from tracker.search import build_judge_messages, keyword_matches, parse_judge

ROWS = [
    {"provider": "Cloudflare", "service": "Workers AI", "metric": "Neurons"},
    {"provider": "Cloudflare", "service": "Workers", "metric": "Worker size"},
    {"provider": "Cloudflare", "service": "Workers KV", "metric": "keys written"},
    {"provider": "Cloudflare", "service": "AI Gateway", "metric": "logs stored"},
]


def services(rows):
    return [r["service"] for r in rows]


def test_every_word_must_match_a_whole_word():
    assert services(keyword_matches("Workers AI", ROWS)) == ["Workers AI"]
    # "ai" must not match inside "Cloudflare"
    assert services(keyword_matches("ai", ROWS)) == ["Workers AI", "AI Gateway"]


def test_quoted_phrase_must_be_consecutive():
    assert services(keyword_matches('"AI Workers"', ROWS)) == []
    assert services(keyword_matches('"Workers AI"', ROWS)) == ["Workers AI"]


def test_a_question_matches_nothing_and_falls_through():
    assert keyword_matches("how many keys can I write per day", ROWS) == []
    assert keyword_matches("   ", ROWS) == []


def test_judge_prompt_numbers_candidates():
    user = build_judge_messages("object storage?", [{"provider": "Cloudflare", "service": "R2", "metric": "storage", "limit": "10 GB-month", "period": "per month", "page": "Workers pricing"}])[1]["content"]
    assert "Question: object storage?" in user
    assert "[0] Cloudflare R2: storage is 10 GB-month per month (from Workers pricing)" in user


def test_parse_judge_keeps_valid_unique_ids_in_order():
    assert parse_judge({"response": {"ids": [3, 1, 3, 99, -1, "2", "x"]}}, 4) == [3, 1, 2]
    assert parse_judge({"response": '{"ids": [0]}'}, 1) == [0]
    assert parse_judge({"response": "not json"}, 1) == []
    assert parse_judge({"response": {}}, 3) == []
