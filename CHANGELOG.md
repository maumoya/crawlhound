# Changelog

## Unreleased

- Benchmark re-run 2026-11-10 on current tools: Crawlee 3.18.2, Crawl4AI 0.9.5, Scrapy 2.20.0 (Twisted 26.10.0);
  every image clears osv-scanner. crawlhound still reads every item with every field right on all 4 targets.

## 0.3.0 (2026-11-10)

- **GitHub Action** (`action.yml`): crawl a site in CI and fail when pages, items or sitemap coverage fall short;
  outputs `pages` and `items`, writes the map to the job summary. Self-tested in CI (one pass, one asserted failure).
- New demo: an illustrated spider walks the site web, blocks the checkout POST and carries 6 items home.
- Fuzzing: Atheris target for the robots.txt, WebSocket frame, URL and schema parsers, run 60 s on every PR.
- Benchmark images pin every pip and npm dependency by hash (`requirements.txt`, `package-lock.json`).

## 0.2.0 (2026-11-10)

- Renamed from site-mapper to **crawlhound**: it crawls data, not just maps. Repo is now maumoya/crawlhound
  (old links redirect), the skill is `/crawlhound`, the install is `crawlhound@crawlhound`, the user agent is
  `crawlhound/0.2`. No behaviour change.
- CI runs the unit and end-to-end tests on every PR.

## 0.1.0 (2026-11-09)

- Read-only spider (`scripts/spider.py`): headless Chrome over the DevTools protocol, one origin, robots.txt
  (RFC 9309, Crawl-delay) and 1 request per second, every non-GET/HEAD request failed in the browser.
- Data extraction: a JSON schema file (`--extract schema.json`, checked with `--check-schema`) or a preset:
  text, tables, links, prices, product, article. No contacts preset, on purpose.
- Site map: pages, SPA states, routes, forms, GET APIs, blocked writes and a coverage verdict (`map.json`, `map.md`).
- Infinite scroll (`--scroll N`), page filter (`--include REGEX`), `--no-click`, `--terms-ok` required off loopback.
- Benchmark (`bench/`): a loopback fixture plus three public sandbox targets, scored against ground truth for
  Crawlee, Crawl4AI, Scrapy, Katana and Firecrawl.
