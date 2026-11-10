# Competitor admission

Every competitor runs in a disposable container: pinned image digest, pinned package versions, a non-root user,
no secrets, no host folders except a fresh temp dir under `~/.cache` (deleted after the run). The loopback fixture
server binds to 127.0.0.1 only; containers reach it through colima's host alias (`host.lima.internal`), so nothing
is exposed to the LAN. Clean-room: tools were run, never copied.

| Tool | Version | License | Image / install | Status |
|---|---|---|---|---|
| crawlhound (this repo) | 0.1.0 | MIT | host Python + Playwright | run |
| Crawlee | crawlee 3.13.3 + playwright 1.48.2 (linkedom pinned 0.16.11: newer releases pull ESM-only deps that break `require`) | Apache-2.0 | `mcr.microsoft.com/playwright:v1.48.2-jammy@sha256:96dc479be9a603227fafc7126bd7d5e8152ae3e63749f377b2c49a4314b28ae1` | run |
| Crawl4AI | 0.6.3 + playwright 1.49.1 | Apache-2.0 | `mcr.microsoft.com/playwright/python:v1.49.1-jammy@sha256:a34c7c0c74cc5c94f39bedbee2cb220b1e0c0408c78610eade5ab1db32c3294f` | run |
| Scrapy | 2.13.3 + Twisted 24.11.0 (newer Twisted removed a private import Scrapy uses) | BSD-3-Clause | `python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` | run |
| Katana | v1.1.3 | MIT | `projectdiscovery/katana:v1.1.3@sha256:ecf5107b693955fba6edbbf489057e1ea075ace60fadfc6d8441ac45384ee279` | run (mapping only: no field extraction) |
| Firecrawl | hosted API via the Firecrawl MCP, 2026-11-09 | AGPL-3.0 (self-host); hosted service used | hosted | run on public targets; fixture not run (a hosted service cannot reach loopback) |
| Browsertrix Crawler | — | AGPL-3.0 | — | not run: a WARC archiver with no field extraction, so it cannot be scored on items |

The Docker files are in `bench/docker/`. Rebuild with `bench/run.sh --build`.
