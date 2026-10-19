# Cloudflare Free Tier

Cloudflare's Free plan is the backbone of a pay-nothing stack: compute, a SQL database, object storage, queues and AI inference, with no credit card and no way to be billed by accident. This page covers what you get, where the limits bite, and how to get set up.

Numbers were taken from Cloudflare's docs on 2026-10-18. The [tracker](how-it-works.md) re-reads them every week, and its [live page](https://free-tier-tracker.free-as-in-tier.workers.dev) has the current values. They change, so check the linked pages before you design around one.

## What you get

| Service | What it is | Free allowance |
| --- | --- | --- |
| [Workers](https://developers.cloudflare.com/workers/) | Serverless functions at the edge | 100,000 requests/day, 10 ms CPU per request, 128 MB memory, 50 subrequests per request, 100 Workers per account, 5 cron triggers per account |
| [Pages](https://developers.cloudflare.com/pages/) | Static sites, with Functions billed as Workers | 500 builds/month, 1 concurrent build, 20-minute build timeout, 20,000 files per site, 25 MiB per file, 100 custom domains per project, 100 projects |
| [D1](https://developers.cloudflare.com/d1/) | Serverless SQLite | 5 million rows read/day, 100,000 rows written/day, 5 GB total |
| [Workers KV](https://developers.cloudflare.com/kv/) | Global key-value store | 100,000 reads/day, 1,000 writes/day, 1,000 deletes/day, 1,000 lists/day, 1 GB stored |
| [R2](https://developers.cloudflare.com/r2/) | S3-compatible object storage | 10 GB-month stored, 1 million Class A (write) and 10 million Class B (read) operations/month, egress always free |
| [Durable Objects](https://developers.cloudflare.com/durable-objects/) | Stateful, strongly consistent objects (SQLite backend only on Free) | 100,000 requests/day, 13,000 GB-s duration/day, 5 GB stored |
| [Queues](https://developers.cloudflare.com/queues/) | Message queues | 10,000 operations/day, 24-hour message retention |
| [Workflows](https://developers.cloudflare.com/workflows/) | Durable multi-step jobs | Requests shared with Workers, 3,000 steps/day, 1 GB-month stored |
| [Workers AI](https://developers.cloudflare.com/workers-ai/) | Model inference on Cloudflare's GPUs | 10,000 neurons/day (Cloudflare's usage unit) |
| [Vectorize](https://developers.cloudflare.com/vectorize/) | Vector database | 5 million stored and 30 million queried vector dimensions/month |
| [Hyperdrive](https://developers.cloudflare.com/hyperdrive/) | Pooled connections to an external Postgres or MySQL | 100,000 queries/day |
| [Workers Logs](https://developers.cloudflare.com/workers/observability/logs/workers-logs/) | Built-in logging | 200,000 log events/day, 3-day retention |
| [Browser Rendering](https://developers.cloudflare.com/browser-rendering/) | Headless Chrome on Cloudflare's network | 10 minutes of browser time/day, 3 concurrent browsers |

Static asset requests served by Workers are free and unlimited.

## How the limits behave

Most daily limits (Workers, KV, D1, Hyperdrive) reset at 00:00 UTC.

Going over a limit doesn't produce a bill on the Free plan. It produces failures. A Worker over its daily request quota returns [Error 1027](https://developers.cloudflare.com/workers/platform/limits/#daily-requests), or is bypassed entirely if its route is set to fail open. That's the property that makes this tier safe for side projects: the worst case is an outage, not an invoice.

One service has a catch: **R2 needs a payment method to turn on.** Creating a bucket from the CLI fails with `Please enable R2 through the Cloudflare Dashboard`, and enabling it there goes through a checkout flow that asks for a card, even though usage inside the free allowance isn't charged. Every other service on this page worked on an account with no card at all.

Two limits shape the design more than the rest:

- **10 ms of CPU per request.** Time spent waiting on I/O (fetches, database queries) doesn't count, so typical API handlers fit easily. Image processing, big JSON transforms or crypto-heavy work doesn't.
- **1,000 KV writes per day.** KV is built for read-heavy data such as config, feature flags and cached responses. Anything write-heavy goes in D1, which allows 100,000 writes a day.

## Signing up

1. Go to [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up).
2. Enter an email and a password, then click the link in the verification email. There's no payment step for the main Free plan.
3. A domain is optional. Workers get a free `*.workers.dev` subdomain and Pages gets `*.pages.dev`. To use your own domain later, add it in the dashboard, choose the Free plan, and point your registrar's nameservers at the two Cloudflare gives you.

One exception: [Zero Trust](https://developers.cloudflare.com/cloudflare-one/), Cloudflare's access-control product, reportedly asks for payment details even on its free plan, without charging them. Nothing in an app stack needs it.

## Connecting this machine

Everything after sign-up runs through [`wrangler`](https://developers.cloudflare.com/workers/wrangler/), Cloudflare's CLI. It isn't installed globally here, and doesn't need to be, because `npx` fetches it:

```bash
npx wrangler login     # opens a browser to authorize this machine, once
npx wrangler whoami    # confirms which account you're logged in to
```

`wrangler login` opens the authorization page in your *default* browser. If the account you signed up with lives in a different browser, print the link instead and open it there:

```bash
npx wrangler login --browser=false   # prints the URL; it keeps waiting on localhost:8976
open -a Vivaldi '<the printed URL>'
```

Closing the page or clicking **Deny** fails the login with `Consent denied`, and you just run it again.

After that, the usual building blocks are one command each:

```bash
npx wrangler init my-worker           # scaffold a Worker
npx wrangler d1 create my-db          # create a D1 database
npx wrangler r2 bucket create my-bkt  # create an R2 bucket
npx wrangler kv namespace create MY_KV
npx wrangler deploy                   # deploy the Worker in the current directory
```

## Sources

- [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/), which lists the free quotas for KV, D1, R2, Durable Objects, Queues, Workflows, Vectorize, Hyperdrive and Logs
- [Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)
- [Pages limits](https://developers.cloudflare.com/pages/platform/limits/)
- [Browser Rendering pricing](https://developers.cloudflare.com/browser-rendering/platform/pricing/)
- [R2 get started](https://developers.cloudflare.com/r2/get-started/), for the checkout flow that enables R2
- [Cloudflare plans](https://www.cloudflare.com/plans/)
- [freetier.wiki on Cloudflare](https://freetier.wiki/services/cloudflare/), for the sign-up flow and billing risk
- [Where Cloudflare Zero Trust stops being free (2026)](https://zerometric.net/research/cloudflare-zero-trust-free-plan-limits-2026/)
