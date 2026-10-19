# free-as-in-tier
Build cloud-based systems using free resources only

![A cyberpunk sentinel scanning a wall of cloud free-tier quotas under a glowing $0](images/hero.png)

## Free Tier Tracker

The first system here watches the free tiers of cloud providers, and runs on one. Every Monday it checks each provider's limits pages, reads the new numbers out of any page that changed with a model, and records what moved. You can browse the current limits, search them in plain language, and see what changed and when. It runs on Cloudflare's Free plan using Workers, Cron Triggers, Queues, Workflows, D1, KV, Workers AI, Vectorize, Durable Objects and static assets, with no credit card on the account.

- Live: <https://free-tier-tracker.free-as-in-tier.workers.dev>
- How it works: [docs/how-it-works.md](docs/how-it-works.md)

## Providers

- [Cloudflare](docs/cloudflare.md): Workers, Pages, D1, KV, R2, Queues, Workers AI and more, no credit card needed
