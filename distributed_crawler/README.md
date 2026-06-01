# Distributed Crawler

An enterprise-oriented distributed crawler scaffold built around `aiohttp` and `asyncio`.

## Included capabilities

- Master + Worker topology
- Redis-backed priority queues
- Consistent-hash worker assignment
- Bloom-filter task dedupe
- Dead-letter queue for exhausted failures
- Checkpoint + state recovery
- Task/request/worker trace headers
- Exponential backoff retries
- Domain-aware rate limiting
- Domain-level circuit breaker
- Proxy pool with health verification
- Proxy scoring with weighted selection
- Shared session store with Redis + local backup
- Result aggregation with JSONL, MongoDB, or PostgreSQL sinks and URL-hash dedupe
- APScheduler-driven periodic task submission

## Safety notes

This project intentionally keeps high-risk account automation, captcha bypass, and anti-protection evasion as extension points only.
Use it only against systems you are authorized to access and collect from.

## Directory layout

```text
distributed_crawler/
  config.example.yaml
  requirements.txt
  crawler/
    auth.py
    backoff.py
    bloom.py
    config.py
    hash_ring.py
    http.py
    master.py
    models.py
    proxy.py
    queue.py
    rate_limit.py
    results.py
    scheduler.py
    sessions.py
    signatures.py
    spider.py
    state.py
    worker.py
  scripts/
    run_master.py
    run_worker.py
```

## Quick start

1. Copy `config.example.yaml` to `config.yaml`.
2. Start Redis.
3. Install dependencies from `requirements.txt`.
4. Start the master:

```bash
python scripts/run_master.py --config config.yaml
```

5. Start one or more workers:

```bash
python scripts/run_worker.py --config config.yaml --worker-id worker-1
python scripts/run_worker.py --config config.yaml --worker-id worker-2
```

6. Submit a task:

```bash
curl -X POST http://127.0.0.1:8088/tasks ^
  -H "Content-Type: application/json" ^
  -d "{\"url\":\"https://httpbin.org/get\",\"spider\":\"generic_fetch\"}"
```

## Master endpoints

- `POST /tasks`
- `POST /heartbeat`
- `GET /workers`
- `GET /stats`
- `GET /health`
- `GET /dashboard` (feature and runtime panel)

## Failure handling and tracing

- Bloom sizing uses `master.bloom_capacity` and `master.bloom_false_positive_rate` to calculate bit count and hash count automatically. Set `master.bloom_hashes` only when you need to override the computed optimum.
- Final failed tasks are appended to `master.dead_letter_queue_name` with the original task, final result, retry count, worker ID, error, and HTTP status.
- Worker requests include `X-Task-Id`, `X-Request-Id`, and `X-Worker-Id` headers. Result payloads also include `request_id` for easier log/result correlation.

## Stability controls

- Proxy endpoints track success count, failure count, latency, cooldown, and a computed score. Workers choose healthy or cooled-down proxies with weighted random selection.
- `circuit_breaker` protects each domain independently. After repeated transport errors or 5xx responses, the domain opens briefly, then moves to half-open for recovery probing.
- Result storage writes a stable `url_hash`. MongoDB and PostgreSQL enforce uniqueness with upsert behavior; JSONL keeps a sidecar `.url_hashes` index.

## Auto login and shared sessions

- Enable `auth.enabled: true` and configure per-domain credentials in `config.yaml`.
- Worker marks sessions expired based on configured status codes/body keywords.
- When expired, worker triggers `AuthProvider.refresh_session()` and writes refreshed cookie to Redis + local backup via `DualSessionStore`.
- All workers read the same session keys (`session.redis_prefix`), so login state is shared.

## Extension points

- `crawler.auth.AuthProvider`
- `crawler.signatures.SignatureProvider`
- `crawler.spider.BaseSpider`
