"""The pages the tracker watches.

Adding a provider is adding an entry here. Prefer a page's plain-text or markdown form
when the provider publishes one: it's a fraction of the HTML's size, so hashing and
extraction stay well inside the Free plan's 10 ms CPU budget.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    id: str
    title: str
    url: str


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    pages: tuple[Page, ...]


_CF_DOCS = "https://developers.cloudflare.com"

PROVIDERS: tuple[Provider, ...] = (
    Provider(
        id="cloudflare",
        name="Cloudflare",
        pages=(
            Page("cloudflare-workers-pricing", "Workers pricing", f"{_CF_DOCS}/workers/platform/pricing/index.md"),
            Page("cloudflare-workers-limits", "Workers limits", f"{_CF_DOCS}/workers/platform/limits/index.md"),
            Page("cloudflare-workers-ai-pricing", "Workers AI pricing", f"{_CF_DOCS}/workers-ai/platform/pricing/index.md"),
            Page("cloudflare-pages-limits", "Pages limits", f"{_CF_DOCS}/pages/platform/limits/index.md"),
            Page(
                "cloudflare-browser-rendering-pricing",
                "Browser Rendering pricing",
                f"{_CF_DOCS}/browser-rendering/platform/pricing/index.md",
            ),
        ),
    ),
)


def all_pages() -> list[tuple[Provider, Page]]:
    return [(provider, page) for provider in PROVIDERS for page in provider.pages]


def find_provider(provider_id: str) -> Provider | None:
    return next((p for p in PROVIDERS if p.id == provider_id), None)


def find_page(page_id: str) -> tuple[Provider, Page] | None:
    return next(((pr, pg) for pr, pg in all_pages() if pg.id == page_id), None)
