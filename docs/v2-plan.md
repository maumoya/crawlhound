# Site mapper v2 gaps: how top crawlers solve them (2026-11-09)

Scope: read-only study of official docs and GitHub source (shallow clones for reading, nothing built, installed or run).
No request was sent to any host on the private deny-list or any Supabase host. Clean-room: patterns only, no code, selectors, wordlists or text copied.
"Not verified" = I did not see it in the source I read. Builds on 2026-11-09-site-mapper-crawler-research.md (not repeated).

## 0. Licenses (from each repo LICENSE file)

| Tool | License | Note |
|---|---|---|
| Katana | MIT (LICENSE.md) | uses a ProjectDiscovery fork of jsluice (go.mod); jsluice upstream MIT per the earlier research |
| Crawlee | Apache-2.0 (LICENSE.md, GitHub spdx) | |
| browsertrix-crawler | AGPL-3.0 (LICENSE) | reference only |
| browsertrix-behaviors | AGPL-3.0 (LICENSE.md, GitHub spdx) | reference only |
| Crawl4AI | Apache-2.0 (LICENSE) | the earlier note about a required attribution badge is not rechecked here |
| Scrapy | BSD-style (LICENSE text "Copyright (c) Scrapy developers", redistribution clauses; SPDX id not read) | |
| scrapy-playwright | BSD-style (LICENSE, Copyright 2020 Eugenio Lacuesta; SPDX id not read) | |

Because browsertrix is AGPL, we only take the idea level (what it observes), never structure or text.

## 1. JS route mining (paths and router manifests in loaded bundles)

**Katana** (MIT). Two separate paths, both off by default.
- `-jc` (ScrapeJSResponses) adds regex parsers: one over inline `<script>` text, one over JS/CSS response bodies, one generic body scraper. Regex is a single relative-endpoint pattern applied after un-escaping `\/`. Source: pkg/engine/parser/parser_generic.go (InitWithOptions), pkg/engine/parser/parser.go (`scriptJSFileRegexParser`), pkg/utils/regex.go.
- `-jsl` (ScrapeJSLuiceResponses) parses inline scripts and JS files with jsluice (tree-sitter AST), keeping the finding type (e.g. "jsluice-<type>") as the edge label. JS files are chosen by `.js` suffix or a `/javascript` content type; `.css` is skipped first; a long regex list of well-known library file names (jquery, react, vendor, analytics, ad networks) is skipped to save memory. Source: pkg/utils/jsluice.go, parser_generic.go.
- Validation before visiting: every hit becomes a navigation request with source tag ("js"/"script") and goes through the normal queue, where `ScopeManager.Validate` (in-scope / out-of-scope rules) runs and the URL is skipped if out of scope. Source: pkg/engine/common/base.go (`ValidateScope`, "not in scope. skipping"). No "does it look like a real route" check, no HEAD probe (not verified beyond what I read).

**Crawlee** (Apache-2.0). No bundle mining. Only DOM link extraction plus `enqueueLinks` filters and click discovery that records `history.pushState` calls (see earlier research). Source searched: packages/*/src (no JS-source parsing found).

**browsertrix-crawler / behaviors** (AGPL). No bundle mining. Links come from `document.querySelectorAll(selectLinks)` in each frame, each URL passed to the crawler's scope check (`__bx_addLink(url, alwaysObeyScope)`). Source: browsertrix-behaviors src/index.ts (`extractLinksActual`), src/lib/utils.ts (`addLink`, `addLinkBatch`), browsertrix-crawler src/crawler.ts (`extractLinks`). Scope = seed-derived include/exclude rules (details of scope not read).

**Crawl4AI** (Apache-2.0). No bundle mining found (grep for route/router/js-link extraction in crawl4ai/*.py: nothing). Discovery is HTML links, sitemaps, Common Crawl.

**Scrapy / scrapy-playwright** (BSD). No JS parsing. Scrapy only follows HTML links; scrapy-playwright (docs/pages.rst) only runs user-written page methods.

### Plan for us (gap 1)
- Source of bundles: do NOT fetch them again. Enable `Network` and keep a small table of `script`/`other` responses whose URL host is same-origin; after `Page.loadEventFired` + settle, call `Network.getResponseBody(requestId)` for same-origin JS only (skip cross-origin, skip libraries by file-name hint, cap body 2 MB and 40 bundles per site, 200 KB scanned per inline script). Zero extra requests, so robots and 1 req/s are untouched. Also take inline `<script>` text via the snapshot.
- Extraction, stdlib `re` only, two tiers: (a) quoted string literals that start with `/` and look like a path (no spaces, no `//`, length 2 to 120, no file extension in a static-asset list, not `/api/`-only? keep them, but tag kind=api); (b) router-manifest shapes: `path:"/x"` object keys, `to:"/x"`, `href:"/x"`, `navigate("/x")`, `route("/x")`, `<Route path=`. Rebuild simple concatenations like `"/a/"+id` as `/a/{id}` (the jsluice EXPR idea) and never fetch templated paths (record as pattern only).
- Validation (hint -> visit): treat every hint as untrusted. Steps: normalize against page URL; same-origin only; `url_denied` and `host_denied`; robots allowed; method GET; DENY label regex applied to the path (logout, delete, etc.); dedupe against known URLs. Then probe through the SPA only if cheap: in the already-loaded page, run `history.pushState` on the path and fingerprint (still no request), or `Page.navigate` at the normal 1 req/s. Mark each hint `verified` (navigation gave a non-404 structural state different from the SPA shell hash), `shell-same` (server returned the same shell for any path, so it is a client route we cannot confirm) or `rejected`. Report hints separately from visited pages so a wrong guess never looks like a finding.
- Risks: bundles contain API paths (`/api/users/delete`); we only GET and the Fetch domain already fails non-GET, but GET endpoints with side effects exist, so API-kind hints are listed, never visited. Source maps (`sourceMappingURL`): record the URL only, do not fetch (extra request, may leak source; owner decision).
- Caps: 40 bundles, 2 MB each, 500 hints per site, 50 hint probes per run, probe depth 1 (hints found while probing are not probed again).
- Loopback test: fixture with bundle `app.js` containing `routes=[{path:"/team"},{path:"/admin/delete-all"},{path:"/u/"+id}]` and `fetch("/api/logout")`. Expect: `/team` verified, `/admin/delete-all` rejected by DENY, `/u/{id}` pattern-only, `/api/logout` listed as api not visited; fixture request log shows `app.js` requested exactly once.

## 2. Scroll and infinite loading

**Katana**: no scroll logic found in pkg/engine/hybrid/crawl.go (grep scroll: none). Not verified elsewhere.

**Crawlee** (Apache-2.0). `infiniteScroll(page, options)` in packages/playwright-crawler/src/internals/utils/playwright-utils.ts (puppeteer twin in puppeteer-utils.ts). Loop: wheel by the full `document.body.scrollHeight`, wait 250 ms, optional small scroll-up (`scrollDownAndUp`), optional click of a "load more" button by selector, optional user `stopScrollCallback`. Stop when no new xhr/fetch/websocket/other requests were seen for `waitForSecs` (default 4) consecutive seconds, or `timeoutSecs` (0 = none) passes, or `maxScrollHeight` (0 = none) is reached. Defaults are unbounded, which is the lesson: always set caps.

**browsertrix-behaviors** (AGPL). `AutoScroll` in src/autoscroll.ts. Pre-check `shouldScroll`: only scrolls if the page has scroll listeners (via `getEventListeners` when the devtools API exists) or has 2+ frames; probes by scrolling to ~98% and seeing whether scrollHeight or in-flight fetches change. Then scrolls in small steps (min(10% viewport, 30 px) every 75 ms), clicks a visible "show more" element found by text (ignored for good if it adds no height), waits a growing time per segment, and stops after the position is unchanged twice. It also stops and records the new URL if `location.pathname` changes. The overall cap is the crawler's per-page behavior timeout (`behaviorTimeout` in crawler.ts; its default value not read). Also flags the reverse case: if the page loaded already scrolled down, scroll up.

**Crawl4AI** (Apache-2.0). Two features in crawl4ai/async_crawler_strategy.py. `scan_full_page`: scroll one viewport at a time, `scroll_delay` between steps, re-read page height each step, hard cap `max_scroll_steps` (code default 10 when unset). `VirtualScrollConfig` (async_configs.py): caller names a container selector; scrolls it `scroll_count` times by container height / page height / fixed pixels, waits `wait_after_scroll`, compares container HTML to the previous HTML to tell "unchanged", "appended" and "replaced" (virtualized lists), and collects the replaced chunks so rows that were recycled are not lost.

**Scrapy / scrapy-playwright** (BSD). Docs only show a user-written page method doing `window.scrollBy(0, document.body.scrollHeight)` (docs/examples.rst, docs/pages.rst). No stop logic.

### Plan for us (gap 2)
- Run after the first snapshot of a page and before click probes, once per URL state (not per click state). CDP: `Runtime.evaluate` for scroll position and heights; `Input.dispatchMouseEvent` type `mouseWheel` is optional, `window.scrollBy` is enough and cheaper. Use `Network.requestWillBeSent` counts (xhr/fetch) as the "something loaded" signal like Crawlee, plus `scrollHeight` and count of structural elements (our `sig` counts) as the DOM signal.
- Algorithm: step = 0.8 x viewport; after each step wait for `settle(quiet=0.4)`; compute new links + `sig` size. Stop when ANY: 2 consecutive steps with no new links and no height change; 15 steps; 20 s wall time; 300 new links; URL changed (record it, navigate back); a scroll step triggered a non-GET attempt (already blocked by Fetch, count it). Never click "show more / load more" by text unless the label passes the DENY check and it is a button/anchor outside a form; allow at most 5 such clicks.
- Nested scroll containers: find elements with `scrollHeight > clientHeight + 50` and `overflow-y` auto/scroll (cap 5 per page), scroll those the same way (the virtual scroll idea). For virtualized lists record the union of link `href`s seen across steps, not only the final DOM, because rows are recycled. Compare container `innerHTML` length or a hash of row labels per step to detect appended vs replaced.
- Read-only/robots risk: infinite feeds trigger GET API calls (same origin, counted against the 1 req/s budget? They are not pageloads). Decision: share the existing limiter by sleeping so xhr/fetch bursts stay under ~2 per second (steps are already 0.4 s+ apart); stop if responses are 429/5xx (observe `Network.responseReceived`).
- Caps as above; global: 40 scrolled pages per run.
- Loopback test: fixture page that appends 10 `<a>` items per scroll event up to 5 batches then stops; a second fixture that never stops (endless generator) must stop at 15 steps / 300 links; a third with a virtualized list that keeps 10 rows. Assert links found, step cap, and no POST.

## 3. Shadow DOM

**Katana** (MIT). pkg/engine/hybrid/crawl.go: `DOM.getDocument` with depth -1 and `pierce: true`, then `traverseDOMNode` walks `Children`, `ContentDocument` (iframes) and `ShadowRoots` and builds a pseudo-HTML string, which is parsed with the normal link parsers as a second pass. Failure of this step is logged and the normal page HTML is still used. Closed roots: CDP returns closed shadow roots in `DOM.getDocument` too (this is a CDP behaviour; Katana does not special-case them; not verified in its tests).

**Crawlee** (Apache-2.0). `expandShadowRoots` in packages/utils/src/internals/general.ts (used by `parseWithCheerio` in playwright-utils.ts): in-page JS recursively visits elements with `el.shadowRoot` (open roots only) and appends the root's serialized HTML into the host, option `ignoreShadowRoots` to turn off. Open roots only, by design of the DOM API.

**browsertrix-behaviors** (AGPL). Uses a third-party helper (`query-selector-shadow-dom`, `querySelectorAllDeep`) in autoplay.ts and autofetcher.ts to find media elements; its default link extraction (`extractLinksActual`) uses plain `querySelectorAll`, so links inside shadow roots are not pierced there (from the source I read).

**Crawl4AI** (Apache-2.0). `flatten_shadow_dom` (config flag; crawl4ai/js_snippet/flatten_shadow_dom.js): in-page recursive serializer that walks open shadow roots, resolves `<slot>` using `assignedNodes`, and skips shadow-scoped `<style>`; returns the composed HTML. Closed roots not reachable (no `closed` handling in the file).

**Scrapy / scrapy-playwright**: none found (Playwright's own CSS engine pierces open roots; that comes from Playwright docs, not verified here).

### Plan for us (gap 3)
- Cheapest first: extend `SNAPSHOT_JS` with a helper `deepAll(sel)` that recurses into `el.shadowRoot` (open) up to depth 5 and 2000 hosts; use it for `interactive`, candidates, links, forms, assets. Include shadow content in `sig` with a `shadow` marker so the structural hash changes when a component renders. Selectors for click probes need a path syntax across roots (record as list `[hostSelector, innerSelector]`, and `CLICK_JS` walks it).
- Closed roots: JS cannot see them. CDP can: `DOM.getDocument {depth:-1, pierce:true}` returns `shadowRoots` including closed ones (Katana relies on that), then `DOM.querySelectorAll` per root nodeId or `DOM.resolveNode` + `Runtime.callFunctionOn` to read `href`s and click (click via `DOM.getBoxModel` + `Input.dispatchMouseEvent`). Use CDP only as a fallback when the JS pass finds a custom element (tag with `-`) with no `shadowRoot` and no children, and report these as `closed-root` so the owner knows coverage is partial. Cap: 1 pierce call per page state, nodes capped at 50k.
- Read-only risk: none new (no requests); the DENY label rule must also apply to labels read inside shadow roots (label text lives in `innerText` of the inner element, so reuse the same `label()` helper).
- Loopback test: fixture with an open-root component holding `<a href=/shadow-open>` and `<button>` that toggles a panel; a closed-root component with `<a href=/shadow-closed>`; a nested component (root inside root). Expect all three links, the toggle state detected (hash changes), and closed one flagged `closed-root`.

## 4. iframes

**Katana** (MIT). Same-frame-tree view through `DOM.getDocument pierce:true`: `ContentDocument` of each iframe is traversed (same-origin and, because CDP returns it, also cross-origin frames in the same process; out-of-process cross-origin iframe content is not guaranteed there, not verified). It also listens to `Page.frameNavigated` but only keeps top-level frames (ParentID empty) to catch JS redirects. Source: crawl.go.

**Crawlee** (Apache-2.0). `parseWithCheerio` (playwright-utils.ts): if `page.frames().length > 1` it fetches each `<iframe>` element's `contentFrame()`, takes its body HTML and inlines it into the cheerio snapshot; warns when the live and parsed iframe counts differ; option `ignoreIframes`. Cross-origin frames work only through Playwright's frame handle; nothing is recorded about the frame as an entity (no list of frame URLs).

**browsertrix-crawler** (AGPL). The most complete model (src/crawler.ts, behaviors src/lib/utils.ts `registerFrame`, index.ts `isInTopFrame`). Per page it uses `Target.setAutoAttach {autoAttach, waitForDebuggerOnStart, flatten}` for out-of-process frames, tracks `Runtime.executionContextCreated/Destroyed` mapping `frameId` to a default context id, and each frame's injected script calls a `Runtime.addBinding` function (`registerFrame`) so the crawler learns about dynamically added frames. For each frame context it runs a check via `Runtime.evaluate({contextId})` returning the frame element tag, location and whether it is top; it skips non-IFRAME/FRAME embeds (PDF, plugins), `about:blank` and empty URLs, and applies a frame-URL include rule (scope). Link extraction and behaviors then run in every accepted frame context; behaviors only run in iframes if a behavior class opts in with `runInIframe`.

**Crawl4AI** (Apache-2.0). `process_iframes` (async_crawler_strategy.py ~line 345): for every `<iframe>`, gets `content_frame()` and its body HTML, and replaces the iframe element with a div containing that HTML; failures logged per iframe. Flag `process_iframes`. No record of frame URL kept beyond the div id.

**Scrapy / scrapy-playwright**: nothing found; plain Scrapy only sees the `<iframe src>` attribute as a link if the spider extracts it.

### Plan for us (gap 4)
- Record frames as entities, not just merged text: for each page state, `Page.getFrameTree` gives `frame.id`, `url`, `securityOrigin`, `parentId`; store list `{url, origin, same_origin, depth, sandbox attr, visible}` in the state. Cap depth 3, 20 frames per page.
- Same-origin frames (same `securityOrigin`): the frame is in our session. Use `Page.createIsolatedWorld {frameId}` (or take `Runtime.executionContextCreated` default context per `auxData.frameId`) then `Runtime.evaluate {contextId}` running `SNAPSHOT_JS`; merge results with a `frame` path prefix and include frame content in the structural hash. Click probes inside a frame run in that context.
- Cross-origin frames: do NOT drive them by default. Record URL, origin and `src`; scope check says whether the origin is in the allowed host list (our `host_denied`/`url_denied`). If the origin is in scope (e.g. a sibling subdomain the owner allowed), map it by its own URL with the normal queue (robots for that origin, 1 req/s per origin); if not in scope, list it as an external embed only (third-party widgets, payment, ads) and never click inside. For out-of-process frames enable `Target.setAutoAttach {autoAttach:true, waitForDebuggerOnStart:false, flatten:true}` only if we decide to read in-scope cross-origin frames; then attached sessions arrive with `sessionId` and our existing `Fetch.enable` and `GUARD_JS` (`Page.addScriptToEvaluateOnNewDocument`) must be re-applied on each child session before it resumes, otherwise a child session is not read-only. This is the main risk, so v2 default is record-only for cross-origin.
- Skip: `about:blank`, PDF/object embeds, frames whose `src` is `javascript:` or `data:`; follow browsertrix's tag check idea.
- Read-only risk: frame navigation requests pass through the same Fetch interception on the page session; child sessions do not inherit it (point above). Forms in frames: same DENY and "inside a form" skip.
- Loopback test: fixture A on port 1 embeds (a) same-origin iframe with a link and a toggle button, (b) iframe from a second loopback port (cross-origin) with a link, (c) nested iframe depth 4. Expect: (a) link and toggle found, hash changes; (b) recorded as cross-origin, not entered (request log shows only the frame document load, no clicks, no POST); (c) depth cap reported. Run again with cross-origin port added to allowed hosts to test the opt-in path and check Fetch interception is active on the child session (a POST from inside the child fixture must be failed).

## 5. Suggested build order (smallest, safest first)
1. Shadow open-root traversal in `SNAPSHOT_JS` (pure JS, no new requests).
2. Frame tree recording + same-origin iframe snapshots.
3. Scroll with caps (uses existing settle and limiter).
4. JS route mining from `Network.getResponseBody` with hint labelling.
5. Closed-root CDP fallback and opt-in cross-origin frames (needs per-session guard work; ADR first).

## 6. Not verified (summary)
- Katana: size/depth caps on jsluice input beyond the skip list; behaviour for cross-origin out-of-process iframes in `DOM.getDocument`; exact scope rules applied to JS-derived hints.
- Crawlee: nothing on robots in these gap paths; no JS mining exists.
- browsertrix: default `behaviorTimeout` value; scope rule details; shadow handling in link extraction is inferred from `querySelectorAll` use only.
- Crawl4AI: attribution requirement text; no robots detail read.
- Scrapy and scrapy-playwright: SPDX id of license not read; Playwright's own shadow/iframe selector behaviour is not verified here (docs not fetched).
- CDP method details (pierce including closed roots, `Page.createIsolatedWorld`, setAutoAttach semantics) come from my knowledge of the protocol and Katana/browsertrix usage, not from the protocol docs in this pass.
