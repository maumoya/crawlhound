# Competitor admission

Every competitor runs in a disposable container: pinned image digest, pinned package versions, a non-root user,
no secrets, no host folders except a fresh temp dir under `~/.cache` (deleted after the run). The loopback fixture
server binds to 127.0.0.1 only; containers reach it through colima's host alias (`host.lima.internal`), so nothing
is exposed to the LAN. Clean-room: tools were run, never copied.

| Tool | Version | License | Image / install | Status |
|---|---|---|---|---|
| crawlhound (this repo) | 0.3.0 | MIT | host Python + Playwright | run |
| Crawlee | crawlee 3.18.2 + playwright 1.64.1 (linkedom pinned 0.16.11: newer releases pull ESM-only deps that break `require`; minimatch overridden to 11.0.1 for its advisories) | Apache-2.0 | `mcr.microsoft.com/playwright:v1.64.1-noble@sha256:2f05f8a734509a7b3f26a912639488ee08f58a7b292d14d62271bcff9038768f` | run |
| Crawl4AI | 0.9.5 + playwright 1.64.1 | Apache-2.0 | `mcr.microsoft.com/playwright/python:v1.64.1-noble@sha256:330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031` | run |
| Scrapy | 2.20.0 + Twisted 26.10.0 (PYSEC-2017-83 has no fixed release; ignored in `bench/docker/scrapy/osv-scanner.toml` because Scrapy only runs offline in the bench container) | BSD-3-Clause | `python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` | run |
| Katana | v1.1.3 | MIT | `projectdiscovery/katana:v1.1.3@sha256:ecf5107b693955fba6edbbf489057e1ea075ace60fadfc6d8441ac45384ee279` | run (mapping only: no field extraction) |
| Firecrawl | hosted API via the Firecrawl MCP, 2026-11-09 | AGPL-3.0 (self-host); hosted service used | hosted | run on public targets; fixture not run (a hosted service cannot reach loopback) |
| Browsertrix Crawler | — | AGPL-3.0 | — | not run: a WARC archiver with no field extraction, so it cannot be scored on items |

The Docker files are in `bench/docker/`. Rebuild with `bench/run.sh --build`.
