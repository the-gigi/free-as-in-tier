from tracker.providers import PROVIDERS, all_pages, find_page, find_provider


def test_page_ids_are_unique_and_fit_a_workflow_instance_id():
    ids = [pg.id for _, pg in all_pages()]
    assert len(ids) == len(set(ids))
    # instance id is "<run id>-<page id>", run ids are ~30 chars, and the cap is 100
    assert all(len(i) <= 60 for i in ids)


def test_lookups():
    assert find_provider("cloudflare") is PROVIDERS[0]
    provider, page = find_page("cloudflare-workers-limits")
    assert provider.id == "cloudflare" and page.url.endswith("/index.md")
    assert find_page("nope") is None
