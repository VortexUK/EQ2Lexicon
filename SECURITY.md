# Security Policy

## Reporting a vulnerability

**Please do not report security issues via public GitHub issues.**

Report privately via [GitHub Security Advisories](https://github.com/VortexUK/EQ2Lexicon/security/advisories/new) on this repo. That opens a private channel to coordinate a fix before public disclosure.

Expect an initial response within 7 days. Confirmed issues affecting authentication, data integrity, or token handling are prioritised.

## What this service is

A public FastAPI + React web app plus a Discord bot, deployed on Railway. It:

- Authenticates users via Discord OAuth; sessions are signed cookies (`itsdangerous`).
- Issues per-user **API tokens** (the `api_tokens` table) that the [ACT plugin](https://github.com/VortexUK/EQ2LexiconACTPlugin) uses to upload parses. These grant upload + delete on the holder's own parses — treat them as passwords.
- Proxies the read-only [Daybreak Census API](https://census.daybreakgames.com) and caches responses.
- Stores users + parses in SQLite (`users.db`, `parses.db`), continuously replicated to Cloudflare R2 via Litestream.
- Gates admin endpoints (`/api/admin/*`) behind an allow-list of Discord IDs (`ADMIN_DISCORD_IDS`).

## Assets worth protecting

| Asset | Where | Risk if exposed |
|---|---|---|
| `SESSION_SECRET` / signing key | env var | Session forgery → impersonate any user |
| `DISCORD_TOKEN` | env var | Full control of the bot account |
| API tokens | `api_tokens` table (hashed) | Upload/delete parses as the victim |
| R2 credentials | env vars | Read/overwrite the off-site DB backups |
| `users.db` / `parses.db` | Railway volume + R2 | User Discord IDs, guild data, parse history |

## In scope

- Authentication / session bypass or fixation
- API token forgery, leakage, or privilege escalation (e.g. uploading/deleting another user's parses)
- SQL injection or path traversal in any route
- SSRF via the Census proxy or any server-side fetch
- Admin endpoint access without an allow-listed Discord ID
- Secrets disclosure through logs, error responses, or API payloads
- Rate-limit bypass enabling resource exhaustion

## Out of scope

- Vulnerabilities in the ACT plugin itself (report to [EQ2LexiconACTPlugin](https://github.com/VortexUK/EQ2LexiconACTPlugin))
- Vulnerabilities in upstream services (Discord, Daybreak Census, Railway, Cloudflare)
- Denial of service requiring unrealistic traffic volumes
- Missing security headers with no demonstrated exploit
- Self-XSS or issues requiring a fully compromised client machine

## Good-practice notes for contributors

- All DB access uses parameterised queries (`aiosqlite`) — never string-format user input into SQL.
- Routes that mutate parses check ownership (uploader) or officer/admin role before acting.
- `slowapi` rate-limits sensitive endpoints; don't remove limits without a reason.
- Never log session cookies, API tokens, or the Discord token. Scrub them from error paths.
