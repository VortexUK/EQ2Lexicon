---
name: architecture-review
description: Staff-level architecture and production-readiness review of EQ2Lexicon - boundaries, Postgres, caching, Census, parse ingest, concurrency, multi-server isolation, security, operations. Invoked by the user.
disable-model-invocation: true
---

# EQ2Lexicon architecture review

Review as the engineer who will be responsible for this system in production. This is
not a style or lint review. The aim is to find architectural weaknesses, scaling limits,
correctness and data-consistency risks, failure modes and maintenance traps that become
expensive as the system grows. Do not assume the current design is right because it is
established, and do not invent facts: inspect the code.

## System, as built

- FastAPI backend, React + TypeScript frontend, a Discord bot in the same process.
- One Supabase Postgres database, one schema per data family, reached through the session
  pooler. DDL in `db/migrations/`, applied at startup. No SQLite anywhere.
- Daybreak Census API upstream: slow, rate limited, sometimes down, and it returns data
  only for recently active characters.
- Stale-while-revalidate caches in process; a persistent census store behind them.
- Background loops owned by the app lifespan, some gated by a leader lease.
- Parse uploads from third-party desktop clients: authenticated, signed, untrusted.
- One deployment serves several game servers by subdomain; `world` is the tenancy boundary.
- Railway, with overlapping containers during a deploy.

Start by reading `CLAUDE.md`, then the `.claude/rules/` file and `docs/architecture/` page
for each area in scope. Use the `codemap` skill to enumerate routes, stores, SQL blocks,
tables and background loops instead of discovering them by search.

## Stress the design against these

Traffic at 10x. Many more game servers. Census slow or down for an hour. A raid-night
burst of uploads. A query that takes 500 ms instead of 5 ms. A background task that dies
halfway. Two requests that trigger the same rebuild. A second application instance. A
deploy landing mid-job. A client sending malformed or hostile payloads.

The question is whether the system stays correct and operable as those change, not
whether it works today.

## Method

For each significant path, trace it end to end before judging it:
frontend, route, domain logic, cache, database, Census or background work, persistence,
response. Do the same for the bot and for parse ingest. Then work through the dimensions
below. Skip a dimension that the scope does not touch, and say that you skipped it.

## Dimensions

**1. Boundaries.** Cross-layer imports, domain logic in handlers, SQL outside sidecars,
Discord or Census assumptions leaking into shared code, duplicated business rules, two
sources of truth, canonical primitives being bypassed.

**2. Postgres.** Schema, constraints, indexes, transaction scope, connection lifecycle and
pool budget (session pooler caps clients; containers overlap), migrations (idempotent,
lock-short, never edited after applying), long read transactions that block DDL,
advisory locks, retention and table growth, row level security, backup and restore.

**3. Query cost.** Expected row counts and how each hot query scales. Index support for
filters, joins and ordering, with the write cost of each index justified. N+1 patterns
hidden behind helpers. Unbounded result sets and missing pagination.

**4. Caching.** For every cache: key, scope, TTL, invalidation, behaviour on refresh
failure, behaviour when many requests miss at once, memory bound. Check that every
world-scoped cache key includes the world and that no user-specific data is shared.

**5. Census.** Timeouts, retries and retry amplification, rate limits, partial or malformed
responses, request deduplication, the availability signal and breaker. No request path
should block on Census when a stored copy exists.

**6. Background work and concurrency.** For each loop or task: can it overlap itself, overlap
a deploy, or run on two instances; is it idempotent; is failure visible; is retry
bounded; is its lock process-local or database-backed; can it hold memory or a
transaction open without bound. Untracked `asyncio.create_task` calls deserve suspicion.

**7. Multi-server isolation.** Every query, cache key, background job, Census call and admin
action must carry the intended world. Look for the `WORLD` constant in web code, for
context lost across a thread pool boundary, and for frontend state surviving a server switch.

**8. Parse ingest.** Authentication, signature check, size and decompression limits,
validation, duplicate and replay handling, idempotency, transaction boundaries, per-client
throttles, world attribution, and anywhere "the client says X" is treated as "the server
verified X". Consider what a buggy client retrying in a loop does.

**9. Authentication and authorisation.** Sessions across subdomains, tokens, roles, character
claims, officer and leader checks. For each privileged operation: who may call it, what
identifies the caller and the resource, which world owns the resource, and what stops
one user acting on another's data.

**10. API design.** Response size, pagination, validation, status codes, cache headers,
versioned contracts that must not change, and endpoints where one cheap request causes
disproportionate work.

**11. Frontend.** Duplicate fetching or state, stale server context, races between
requests, effects with wrong dependencies, large lists, drift between backend responses
and frontend types.

**12. Resource bounds.** Unbounded dictionaries, caches, lists, payload retention and task
references. Separate "bounded by design" from "small with today's data"; the second is a finding.

**13. Failure modes.** For each component: the failure, the user impact, the recovery, the
risk. Look for single points of failure, cascades, retry storms, partial writes and
silent degradation.

**14. Operations.** Startup cost as data grows, behaviour during an overlapping deploy,
what a restart loses, whether scheduled work doubles with two instances, observability
when something is wrong, and whether a flood produces one log line or thousands.

**15. Tests.** Whether tests cover the architectural failure modes: concurrent access,
duplicate refresh, Census failure, world isolation, permission boundaries, malformed and
duplicate uploads, rollback. Prefer tests that pin an invariant over tests that exercise lines.

Where it sharpens a finding, quantify the amplification: requests, times queries per
request, times external calls, times cache-miss rate.

## Severity

- **Critical**: cross-server data leakage, an authentication or authorisation bypass,
  unbounded resource use, data corruption, a retryable write that is not idempotent,
  a ranking integrity hole, exposed credentials.
- **High**: N+1 against the database or Census, cache stampede, lock or pool exhaustion,
  unbounded background work, silent data loss, wrong transaction boundaries, one request
  causing disproportionate upstream load.
- **Medium**: duplicated business logic, needless coupling or round trips, weak
  invalidation, designs that are hard to test.
- **Low**: local maintainability.

Do not manufacture findings to fill a section.

## Output

1. **Executive summary**: three to eight bullets covering overall health, the most
   important risks, and what breaks first at 10x.
2. **Findings**, most severe first, each with: location (`path:line`), the problem, why
   it matters in production, the concrete failure or scaling scenario, the evidence in
   the code, a specific recommendation, and your confidence.
3. **Assessment by dimension**, briefly, including what you checked and found sound.
4. **10x analysis**: the five likeliest bottlenecks, ranked.
5. **Recommended changes**: do now, do before significant growth, nice to have.

## Standards for the review itself

- Follow data through the whole system; read callers and callees.
- Separate what you measured or read from what you predict.
- Name the workload that makes a performance concern real.
- Do not recommend a rewrite, a new datastore, a cache server, a queue or microservices
  without showing why the current design cannot carry the load.
- Do not repeat lint findings or nitpick formatting.

The standard: would you be comfortable owning this in production, knowing it may grow
and that failures will happen? If not, say exactly why.
