# How the Free Tier Tracker Works

The tracker watches the pages where cloud providers publish their free-tier limits. Once a week it checks every page, notices when one changed, reads the new limits out of it with a model, and records what moved. D1 is the record. The web UI and a JSON API read from it, so a changed limit shows up there as soon as the check that found it finishes. It all runs on Cloudflare's Free plan, so the system that tracks free tiers costs nothing to run.

This page walks through the design from the trigger to the stored result, then covers the API and UI, the data model, how it stays inside the free limits, and what was measured while building it.

Live: <https://free-tier-tracker.free-as-in-tier.workers.dev>

## The pipeline

```text
  Cron (Mondays 06:00 UTC)  or  POST /api/runs
            │
            ▼
      start_run ──► D1: runs row
            │
            ▼
      Queue "free-tier-checks": one message per page
            │
            ▼
      queue consumer: starts one Workflow instance per page
            │
            ▼
      CheckPage workflow (every step retried on its own)
        1. fetch      GET the page's markdown, normalize it, hash it
        2. compare    hash vs the last one in KV ── unchanged? record it and stop
        3. snapshot   page text into D1, as evidence for whatever changes
        4. extract    Workers AI reads the free-tier limits out as JSON, one call per chunk
        5. store      diff against the page's active quotas, write the result to D1
        6. embed      Workers AI embeddings into Vectorize, for search
        7. remember   the new hash into KV
        8. record     a checks row with what happened
            │
            ▼
      every step reports to the LiveFeed Durable Object ──► browsers over WebSocket
```

Let's go through it in order.

### Triggering a run

A cron trigger in `wrangler.jsonc` fires every Monday at 06:00 UTC and calls `start_run`. The same function backs `POST /api/runs`, which starts a run by hand. That endpoint needs the admin token, because a run can spend Workers AI's daily allowance. A manual run can name specific pages and pass `"force": true` to skip the hash check:

```bash
curl -X POST https://free-tier-tracker.free-as-in-tier.workers.dev/api/runs \
  -H "Authorization: Bearer $(cat ~/.config/free-tier-tracker/admin-token)" \
  -H "Content-Type: application/json" \
  -d '{"pages": ["cloudflare-workers-limits"], "force": true}'
```

`start_run` writes a `runs` row, then sends one queue message per page.

### Why a queue, then a workflow

The cron handler could check every page itself, but one Worker invocation on the Free plan is allowed 50 subrequests. A single page check makes a couple of dozen (the fetch, KV, several D1 calls, a model call per chunk, Vectorize, the feed), so checking pages one after another in a single invocation would hit the cap by the second or third page. The queue fans the run out so each page gets its own invocations.

The queue consumer doesn't do the check either. It starts a [Workflow](https://developers.cloudflare.com/workflows/) instance per page and acknowledges the message. A workflow is a sequence of named steps where each step's result is stored, so when a later step fails, the retry replays the earlier results instead of redoing them. The extraction step is the one this protects most: a failure while embedding doesn't spend the model calls a second time.

The instance id is `<run id>-<page id>`, so a message the queue delivers twice tries to create the same instance, fails with "already exists", and is dropped.

### Fetching and hashing

Each page in `src/tracker/providers.py` points at the provider's markdown version of the page where one exists. Cloudflare's docs serve one at `<page>/index.md`. The Workers limits page is 27 KB as markdown against 250 KB as HTML, and there's no HTML to parse.

`text.normalize` strips the lines that change without the content changing: the "Last updated" stamp, the documentation-index banner and the cache-busting image URL in the front matter. Then the text is hashed with SHA-256. Without that step every docs rebuild would look like a change and spend an extraction.

### Compare, and stopping early

The last hash for each page is in KV under `hash:<page id>`. If the new hash matches, the workflow writes a `checks` row saying "unchanged" and ends. In a typical week most pages end here, and the model never runs for them.

### Extraction

For a changed page, the snapshot goes into D1 first, so every extraction has the exact text it was read from. Then the text goes to Workers AI (`@cf/meta/llama-3.3-70b-instruct-fp8-fast`) in JSON mode, with a schema that asks for one object per free-plan limit: service, metric, limit, period and notes. The prompt asks for numbers copied exactly as written, nothing that applies only to paid plans, and nothing without a number.

Two details came out of testing it against the real pages:

- **Long pages are split.** Given the whole 35 KB Workers pricing page in one call, the model extracted the sections before and after Queues and Workflows and skipped those two. `text.chunks` splits a page at its `##` headings and packs whole sections into chunks of up to 12,000 characters, one call each, and `quotas.merge` combines the results. On that page, chunking took the extraction from 18 quotas to 24.
- **The model is told which names already exist.** Left alone, it renamed things between runs ("log retention" one week, "retention" the next), and each rename read as one quota removed and another added. Every call now includes the service and metric names already recorded for the page, with an instruction to reuse them verbatim.

`quotas.parse_extraction` tolerates the shapes the model returns (an object, or a JSON string, sometimes inside a markdown fence), drops rows missing a required field, and keeps the first of any duplicates.

### Storing and diffing

`quotas.diff` compares the new extraction with the page's active quotas in D1. Quotas are matched on a normalized service and metric, so "Workers KV / keys written" and "workers-kv / Keys Written" are the same quota.

A quota counts as changed when its limit or period changed, compared after normalization. The model varies formatting between runs: R2's Class B allowance came back as "10 million requests" one run and "10 million requests / month" the next, with "per month" as the period both times. `Quota.comparable` drops a period folded into the limit and normalizes spacing and case, and leaves digits and units alone, so a real cut from "10 GB-month" to "5 GB-month" still registers. Notes are ignored entirely, because the model rewords them freely.

`store.apply_extraction` writes the whole result as one D1 batch. A changed quota closes the old row (`active = 0`) and opens a new one, so the `quotas` table holds the full history, and every change also gets a row in `changes`.

The first extraction of a page has nothing to compare against, so everything comes out as "added". That's how the tracker seeds itself, and adding a provider works the same way.

### Search

Each quota becomes a sentence such as "Cloudflare Workers KV: keys written 1,000 per day", which is embedded with `@cf/baai/bge-base-en-v1.5` (768 dimensions) and upserted into Vectorize under a stable id derived from the provider and quota key. Re-extracting a page overwrites its vectors rather than adding duplicates, and a removed quota's vector is deleted.

`GET /api/search?q=...` embeds the question the same way and returns the ten closest quotas. Results are cached in KV for an hour, so a repeated search costs no model call. The cache key includes an index version that the embed step bumps, so an answer cached before new vectors were indexed is never served after.

### Seeing what changed

There's no separate publishing step. A change is a row in D1 the moment the store step commits it, and everything reads from there:

- the UI's **Recent changes** panel and `GET /api/changes`, with the before and after values
- the live feed, which shows each check's result, including how many quotas changed, as it happens
- `GET /api/quotas`, which always returns the current values

Every quota links back to its source page, and the page text the model read is kept in `snapshots`, so any number can be checked against exactly what it was extracted from.

### The live feed

`LiveFeed` is a Durable Object with a single instance named `feed`. Each workflow step posts an event to it, and it forwards each event to every browser connected to `/api/live`. It uses the WebSocket hibernation API, so idle connections don't keep the object running. It keeps the last 50 events in its storage and replays them to a new connection, so the UI isn't empty when it opens. Reporting is best effort: if the feed is unavailable, the check carries on.

## The API and the UI

The UI is a static `public/index.html` served as a Workers static asset. `run_worker_first` is limited to `/api/*`, so page loads never run the Worker and don't count against the 100,000 daily requests.

| Endpoint | What it returns |
| --- | --- |
| `GET /api/providers` | Every tracked page with its last check |
| `GET /api/quotas?provider=cloudflare` | The provider's active quotas |
| `GET /api/changes` | The 50 most recent changes |
| `GET /api/runs` | The 10 most recent runs, with each page's result |
| `GET /api/search?q=...` | The ten quotas closest to the question |
| `GET /api/live` | WebSocket: the live event feed |
| `POST /api/runs` | Start a run. Needs `Authorization: Bearer <admin token>` |

## Data model

The schema is in `migrations/`.

| Table | One row per |
| --- | --- |
| `runs` | Run, cron or manual |
| `checks` | Page per run: hash, whether it changed, quota and change counts, error |
| `snapshots` | Distinct version of a page's text |
| `quotas` | Version of a quota. `active = 1` marks the current one |
| `changes` | Added, removed or changed quota |

## Staying free

| Service | Used for | Use per weekly run (5 pages) | Free allowance |
| --- | --- | --- | --- |
| Workers | API, cron, queue consumer | A few dozen requests | 100,000 requests/day |
| Queues | Fan-out | ~15 operations | 10,000 operations/day |
| Workflows | One instance per page | ≤ 8 steps per page | 3,000 steps/day |
| KV | Page hashes, search cache | ~10 reads, ≤ 7 writes | 100,000 reads, 1,000 writes/day |
| D1 | Everything durable | A few hundred rows written | 100,000 rows written/day, 5 GB |
| Workers AI | Extraction and embeddings | 0 for unchanged pages, ~1,100 neurons if all five change | 10,000 neurons/day |
| Vectorize | Search | ~60 vectors of 768 dimensions | 5M stored dimensions |
| Durable Objects | Live feed | A few dozen requests | 100,000 requests/day |

Workers AI is the only allowance a run gets anywhere near, and only when many pages change on one day. A forced run of all five Cloudflare pages is the worst case, at roughly 11% of the daily neurons.

R2 isn't used. Turning it on goes through a dashboard checkout that asks for a payment method, even though usage inside the free tier isn't charged, and that conflicts with this repo's no-card rule. The snapshots it would hold are small text files, so they live in D1 instead.

## Measured while building it

These came from real runs on 2026-10-19, read from `wrangler tail` and the model's usage reports.

| What | Measured |
| --- | --- |
| Neurons per page extraction | 58 (Browser Rendering pricing, 1.6K tokens) to 460 (Workers pricing, 4 chunks) |
| Wall time per page check | 5 to 21 seconds, almost all of it the model |
| CPU per workflow invocation | 80 to 338 ms |
| CPU per API request | 5 to 31 ms |
| Worker startup time | 453 ms (Python, measured at deploy) |

The CPU numbers are worth knowing. The Workers Free plan lists 10 ms of CPU per invocation, and the workflow invocations used up to 338 ms and still completed with outcome `ok`. That's what happened here, not a promise: if those invocations start failing, CPU is the first suspect, and the Python runtime is the likeliest cost.

Three findings about the platform itself:

- **Cloudflare's queue docs show `queue(self, batch)`, but the runtime calls `queue(self, batch, env, ctx)`.** The two-argument version crashed on every message with `TypeError: Default.queue() takes 2 positional arguments but 4 were given`. The cron handler gets the same three arguments, and its docs do show that.
- **The Workers pricing page says "Vectorize is currently only available on the Workers paid plan"**, then lists Free-plan allowances right below. An index created on this free account works: it holds the tracker's vectors and answers queries.
- **A new `workers.dev` subdomain takes about a minute to answer.** Until then, requests fail with a TLS error (curl exit code 35).

## Running it yourself

You need `uv` 0.12.3 or later and Node.

```bash
uv sync                          # dev dependencies, including pytest
uv run pytest                    # the pure modules: providers, text, quotas
npx wrangler login
npx wrangler d1 migrations apply free-tier-tracker --remote
uv run pywrangler deploy
npx wrangler secret put ADMIN_TOKEN    # any long random string; guards POST /api/runs
```

The resource ids in `wrangler.jsonc` belong to this deployment. To run your own copy, create the resources and paste in the ids they print:

```bash
npx wrangler d1 create free-tier-tracker
npx wrangler kv namespace create free-tier-tracker
npx wrangler queues create free-tier-checks
npx wrangler vectorize create free-tier-quotas --dimensions=768 --metric=cosine
```

## Adding a provider

Add a `Provider` with its pages to `src/tracker/providers.py` and deploy. The next run treats the new pages as first sightings: every quota comes out as "added". Prefer a page's markdown or plain-text form where the provider publishes one. A page that only renders with JavaScript would need Browser Rendering, which the Free plan allows for 10 minutes a day.

## Code layout

| Path | What it holds |
| --- | --- |
| `src/entry.py` | The Worker: HTTP API, cron and queue handlers, the `CheckPage` workflow, the `LiveFeed` Durable Object |
| `src/tracker/providers.py` | The pages being watched |
| `src/tracker/text.py` | Normalizing, hashing and chunking page text |
| `src/tracker/quotas.py` | Extraction prompt and schema, parsing, merging, diffing |
| `src/tracker/store.py` | D1 queries |
| `migrations/` | D1 schema |
| `public/` | The UI |
| `tests/` | pytest for the pure modules |
