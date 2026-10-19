"""Free Tier Tracker entry point.

    cron (weekly) ──► start_run ──► Queue: one message per page
                                        │
                     queue consumer ◄───┘ starts one Workflow per page
                                        │
    CheckPage workflow: fetch → compare hash (KV) → snapshot (D1) → extract (Workers AI)
                        → diff + store (D1) → embed (Workers AI + Vectorize)
                                        │
    every step reports to the LiveFeed Durable Object, which fans out to WebSockets

docs/how-it-works.md walks through each piece.
"""

import json
import secrets
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from js import WebSocketPair
from workers import DurableObject, Response, WorkerEntrypoint, WorkflowEntrypoint, fetch
from workers.workflows import NonRetryableError

from tracker import store
from tracker.providers import PROVIDERS, all_pages, find_page, find_provider
from tracker.quotas import (
    EXTRACTION_SCHEMA,
    Quota,
    build_messages,
    diff,
    merge,
    parse_extraction,
    quota_text,
    vector_id,
)
from tracker.text import chunks, clip, content_hash, normalize

SEARCH_CACHE_TTL = 3600  # seconds; KV's minimum is 60
FEED_NAME = "feed"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def env_get(env, name: str):
    """A missing var or secret is an AttributeError on the JS env; treat it as unset."""
    try:
        value = getattr(env, name)
    except AttributeError:
        return None
    return value if isinstance(value, str) and value else None


def json_response(data, status: int = 200) -> Response:
    return Response(json.dumps(data, default=str), status=status, headers={"content-type": "application/json"})


async def notify(env, event: dict) -> None:
    """Best effort: a feed that's down must never fail a check."""
    try:
        stub = env.FEED.getByName(FEED_NAME)
        await stub.fetch("https://feed/event", method="POST", body=json.dumps({**event, "at": now_iso()}))
    except Exception as exc:  # noqa: BLE001
        print(f"feed notify failed: {exc}")


async def start_run(env, trigger: str, page_ids: list[str] | None = None, force: bool = False) -> dict:
    pages = [(pr, pg) for pr, pg in all_pages() if not page_ids or pg.id in page_ids]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{trigger}-{secrets.token_hex(2)}"
    await store.insert_run(env.DB, run_id, trigger, now_iso(), len(pages))
    if pages:
        await env.CHECKS.sendBatch(
            [{"body": {"run_id": run_id, "provider": pr.id, "page_id": pg.id, "force": force}} for pr, pg in pages]
        )
    await notify(env, {"type": "run", "run_id": run_id, "trigger": trigger, "pages": len(pages)})
    return {"run_id": run_id, "pages": [pg.id for _, pg in pages]}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path.rstrip("/")
        try:
            if path == "/api/live":
                # Hand the WebSocket upgrade straight to the Durable Object.
                stub = self.env.FEED.getByName(FEED_NAME)
                return Response(await stub._binding.fetch(request.js_object))
            if path == "/api/providers" and request.method == "GET":
                return json_response(await self.providers())
            if path == "/api/quotas" and request.method == "GET":
                return await self.quotas(query.get("provider", "cloudflare"))
            if path == "/api/changes" and request.method == "GET":
                return json_response(await store.recent_changes(self.env.DB))
            if path == "/api/runs" and request.method == "GET":
                return json_response(await store.recent_runs(self.env.DB))
            if path == "/api/runs" and request.method == "POST":
                return await self.trigger(request)
            if path == "/api/search" and request.method == "GET":
                return await self.search(query.get("q", "").strip())
            return json_response({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            print(f"error on {request.method} {url.path}: {exc!r}")
            return json_response({"error": "internal error"}, 500)

    async def scheduled(self, controller, env=None, ctx=None):
        result = await start_run(self.env, "cron")
        print(f"cron run {result['run_id']}: {len(result['pages'])} pages queued")

    # The runtime passes env and ctx positionally here, as it does for scheduled, even
    # though Cloudflare's queue docs show `queue(self, batch)`.
    async def queue(self, batch, env=None, ctx=None):
        for message in batch.messages:
            body = message.body
            instance_id = f"{body['run_id']}-{body['page_id']}"[:100]
            try:
                await self.env.CHECK_PAGE.create({"id": instance_id, "params": body})
            except Exception as exc:  # noqa: BLE001
                # A redelivered message whose workflow already exists is fine to drop.
                if "already exists" not in str(exc):
                    print(f"could not start workflow {instance_id}: {exc}")
                    message.retry()
                    continue
            message.ack()

    async def providers(self):
        last = await store.last_checks(self.env.DB)
        return [
            {
                "id": p.id,
                "name": p.name,
                "pages": [{"id": pg.id, "title": pg.title, "url": pg.url, "last_check": last.get(pg.id)} for pg in p.pages],
            }
            for p in PROVIDERS
        ]

    async def quotas(self, provider_id: str):
        provider = find_provider(provider_id)
        if provider is None:
            return json_response({"error": f"unknown provider {provider_id}"}, 404)
        rows = await store.active_quotas(self.env.DB, provider.id)
        titles = {pg.id: pg.title for pg in provider.pages}
        return json_response(
            [
                {
                    "page": titles.get(r["page_id"], r["page_id"]),
                    "service": r["service"],
                    "metric": r["metric"],
                    "limit": r["limit_value"],
                    "period": r["period"],
                    "notes": r["notes"],
                    "since": r["first_seen"],
                }
                for r in rows
            ]
        )

    async def trigger(self, request):
        # Manual runs spend Workers AI neurons, so they need the admin token.
        expected = env_get(self.env, "ADMIN_TOKEN")
        supplied = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        if not expected or not secrets.compare_digest(supplied, expected):
            return json_response({"error": "unauthorized"}, 401)
        body = {}
        if (request.headers.get("content-type") or "").startswith("application/json"):
            body = await request.json()
        result = await start_run(self.env, "manual", body.get("pages"), bool(body.get("force")))
        return json_response(result, 202)

    async def search(self, q: str):
        if not q:
            return json_response({"error": "missing q"}, 400)
        # The index version is bumped whenever vectors change, so new quotas never sit
        # behind an answer cached before they were indexed.
        version = await self.env.KV.get("search:version") or "0"
        cache_key = f"search:{version}:{content_hash(q.lower())[:32]}"
        cached = await self.env.KV.get(cache_key)
        if cached:
            return Response(cached, headers={"content-type": "application/json", "x-cache": "hit"})
        embedded = await self.env.AI.run(env_get(self.env, "EMBED_MODEL"), {"text": [q]})
        result = await self.env.VECTORS.query(embedded["data"][0], {"topK": 10, "returnMetadata": "all"})
        matches = [{"score": round(m["score"], 3), **(m.get("metadata") or {})} for m in result.get("matches") or []]
        payload = json.dumps(matches)
        await self.env.KV.put(cache_key, payload, {"expirationTtl": SEARCH_CACHE_TTL})
        return Response(payload, headers={"content-type": "application/json", "x-cache": "miss"})


RETRY = {"retries": {"limit": 3, "delay": "30 seconds", "backoff": "exponential"}, "timeout": "5 minutes"}


class CheckPage(WorkflowEntrypoint):
    """Checks one page. Each step is retried on its own, and a step that already
    succeeded isn't re-run when a later one fails: its result is replayed."""

    async def run(self, event, step):
        params = event["payload"]
        run_id, page_id, force = params["run_id"], params["page_id"], bool(params.get("force"))
        found = find_page(page_id)
        if found is None:
            raise NonRetryableError(f"unknown page {page_id}")
        provider, page = found
        env = self.env

        try:
            await notify(env, {"type": "check", "run_id": run_id, "page_id": page_id, "status": "fetching"})

            @step.do("fetch", config=RETRY)
            async def fetch_page():
                resp = await fetch(page.url, headers={"User-Agent": "free-tier-tracker", "Accept": "text/markdown, text/plain, */*"})
                if resp.status != 200:
                    raise Exception(f"GET {page.url}: {resp.status}")
                text = normalize(await resp.text())
                return {"text": text, "hash": content_hash(text), "chars": len(text)}

            fetched = await fetch_page()

            @step.do("compare")
            async def compare():
                previous = await env.KV.get(f"hash:{page_id}")
                return {"changed": force or previous != fetched["hash"], "first": previous is None}

            compared = await compare()
            if not compared["changed"]:
                @step.do("record unchanged")
                async def record_unchanged():
                    count = len(await store.active_quotas(env.DB, provider.id, page_id))
                    await store.insert_check(env.DB, run_id=run_id, provider=provider.id, page_id=page_id, checked_at=now_iso(), hash=fetched["hash"], quotas=count)

                await record_unchanged()
                await notify(env, {"type": "check", "run_id": run_id, "page_id": page_id, "status": "unchanged"})
                return {"page_id": page_id, "changed": False}

            await notify(env, {"type": "check", "run_id": run_id, "page_id": page_id, "status": "changed, extracting"})

            @step.do("snapshot")
            async def snapshot():
                await store.save_snapshot(env.DB, page_id, fetched["hash"], now_iso(), fetched["text"])

            await snapshot()

            @step.do("extract", config=RETRY)
            async def extract():
                known = [store.row_to_quota(r) for r in await store.active_quotas(env.DB, provider.id, page_id)]
                parts, neurons = [], 0.0
                for chunk in chunks(clip(fetched["text"])):
                    result = await env.AI.run(
                        env_get(env, "AI_MODEL"),
                        {
                            "messages": build_messages(provider.name, page.title, chunk, known),
                            "response_format": {"type": "json_schema", "json_schema": EXTRACTION_SCHEMA},
                            "max_tokens": 4096,
                            "temperature": 0,
                        },
                    )
                    parts.append(parse_extraction(result))
                    neurons += float((result.get("usage") or {}).get("neurons") or 0)
                quotas = merge(*parts)
                if not quotas:
                    raise Exception("model returned no quotas")
                print(f"{page_id}: extracted {len(quotas)} quotas from {len(parts)} chunks, {neurons:.0f} neurons")
                return [q.__dict__ for q in quotas]

            quotas = [Quota(**q) for q in await extract()]

            @step.do("store")
            async def store_quotas():
                old = [store.row_to_quota(r) for r in await store.active_quotas(env.DB, provider.id, page_id)]
                changes = diff(old, quotas)
                await store.apply_extraction(env.DB, run_id=run_id, provider=provider.id, page_id=page_id, quotas=quotas, changes=changes, now=now_iso())
                return changes.rows()

            change_rows = await store_quotas()

            @step.do("embed", config=RETRY)
            async def embed():
                removed = [vector_id(provider.id, Quota(r["service"], r["metric"], "", "")) for r in change_rows if r["kind"] == "removed"]
                if removed:
                    await env.VECTORS.deleteByIds(removed)
                texts = [quota_text(provider.name, q) for q in quotas]
                vectors = []
                for start in range(0, len(texts), 50):
                    out = await env.AI.run(env_get(env, "EMBED_MODEL"), {"text": texts[start : start + 50]})
                    vectors += list(out["data"])
                await env.VECTORS.upsert(
                    [
                        {
                            "id": vector_id(provider.id, q),
                            "values": list(vec),
                            "metadata": {"provider": provider.name, "service": q.service, "metric": q.metric, "limit": q.limit, "period": q.period, "page": page.title, "url": page.url},
                        }
                        for q, vec in zip(quotas, vectors)
                    ]
                )
                await env.KV.put("search:version", now_iso())
                return len(vectors)

            await embed()

            @step.do("remember hash")
            async def remember_hash():
                await env.KV.put(f"hash:{page_id}", fetched["hash"])

            await remember_hash()

            @step.do("record")
            async def record():
                await store.insert_check(
                    env.DB, run_id=run_id, provider=provider.id, page_id=page_id, checked_at=now_iso(),
                    hash=fetched["hash"], changed=True, quotas=len(quotas), changes=len(change_rows),
                )

            await record()
            await notify(env, {"type": "check", "run_id": run_id, "page_id": page_id, "status": "done", "quotas": len(quotas), "changes": change_rows})
            return {"page_id": page_id, "changed": True, "changes": len(change_rows)}
        except Exception as exc:
            await store.insert_check(env.DB, run_id=run_id, provider=provider.id, page_id=page_id, checked_at=now_iso(), error=str(exc)[:500])
            await notify(env, {"type": "check", "run_id": run_id, "page_id": page_id, "status": "failed", "error": str(exc)[:200]})
            raise


class LiveFeed(DurableObject):
    """One instance, named "feed". Workflows POST events here, and every connected browser
    gets them over a WebSocket. Uses the hibernation API, so idle sockets cost nothing."""

    RECENT = 50

    async def fetch(self, request):
        path = urlparse(request.url).path
        if path.endswith("/event") and request.method == "POST":
            event = json.loads(await request.text())
            recent = list(await self.ctx.storage.get("recent") or [])
            recent = (recent + [event])[-self.RECENT :]
            await self.ctx.storage.put("recent", recent)
            message = json.dumps(event)
            for ws in self.ctx.getWebSockets():
                try:
                    ws.send(message)
                except Exception:  # noqa: BLE001
                    pass
            return Response("ok")
        if (request.headers.get("upgrade") or "").lower() == "websocket":
            client, server = WebSocketPair.new().object_values()
            self.ctx.acceptWebSocket(server)
            # Replay recent events so a fresh page isn't empty.
            for event in list(await self.ctx.storage.get("recent") or []):
                server.send(json.dumps(event))
            return Response(None, status=101, web_socket=client)
        return Response("expected a websocket upgrade", status=426)

    async def webSocketMessage(self, ws, message):
        if message == "ping":
            ws.send("pong")

    async def webSocketClose(self, ws, code, reason, was_clean):
        pass
