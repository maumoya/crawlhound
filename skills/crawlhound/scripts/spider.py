"""crawlhound spider: crawl a site read-only, map its SPA states and pull the data you choose.

Data: `--extract SCHEMA` (a preset name or a JSON schema file, see extract.py) pulls items from every page state
into items.jsonl. Map: what it records: the page tree, SPA states reached by clicking (tabs, modals, sidebar buttons without links),
same-document routes (pushState, hash), forms (fields only, never submitted), asset and GET API URLs (listed,
never bulk-downloaded), visible text per state, and a ledger of every click candidate probed or skipped with
the reason. Coverage is claimed as "complete for this policy", never "100% of the site".

Read-only by construction, in this order:
  1. a hard host deny-list (private hosts, stored hashed, and Supabase hosts) refuses the start URL and every request;
  2. robots.txt (RFC 9309 matching, Crawl-delay honoured) and a same-origin scope gate every page;
  3. every network request the browser makes is paused (CDP Fetch) and answered here: anything but GET/HEAD,
     anything cross-origin, robots-disallowed or matching a destructive URL pattern is failed before it leaves;
  4. WebSocket, sendBeacon and window.open are stubbed in the page, service workers bypassed, downloads denied,
     dialogs dismissed, file choosers never answered;
  5. click-probing skips anything inside a <form>, submit/file inputs, and labels on a destructive deny-list.
Every allowed request waits for the rate limit (default 1 per second, never below robots Crawl-delay; only a
loopback test site may go faster).

Standard library only; drives the Chrome already installed (cdp.py next to this file).
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp as _cdp  # noqa: E402
import extract as _extract  # noqa: E402

USER_AGENT = "crawlhound/0.3 (+https://github.com/maumoya/crawlhound; read-only; robots.txt honoured)"
ROBOTS_TOKEN = "crawlhound"
# Never mapped, never probed, never fetched, whatever the flags say. Private hosts are kept as SHA-256 hashes of
# the registrable host name so the public code enforces the rule without naming them.
DENIED_HOST_HASHES = ("d2760e1ab724201bbb12e8ded28bb601e3ceb139d3d82113f367ca6f6772b62d",)
DENIED_HOST_SUFFIXES = ("supabase.co", "supabase.in", "supabase.com")
DENY_LABEL = re.compile(
    r"\b(submit|pay|payment|checkout|buy|purchase|order|upload|delete|remove|destroy|erase|log ?out|sign ?out|"
    r"book|reserve|send|save|confirm|apply|subscribe|unsubscribe|register|sign ?up|donate|transfer|withdraw|"
    r"publish|post|share|invite|archive|reset|cancel subscription|deactivate|close account)\b", re.I)
DENY_URL = re.compile(r"(log-?out|sign-?out|logoff|delete|remove|destroy|unsubscribe|deactivate|/cancel\b)", re.I)
READ_METHODS = ("GET", "HEAD")
ASSET_TYPES = ("Script", "Stylesheet", "Image", "Font", "Media", "Manifest", "TextTrack")
API_TYPES = ("XHR", "Fetch", "EventSource")
MEDIA_TYPES = ("Image", "Font", "Media")   # skipped by default: politeness, and 3x faster at 1 request/s
SCROLL_STEPS, SCROLL_WAIT = 15, 20.0
ROBOTS_MAX = 500 * 1024
SITEMAP_MAX = 5 * 1024 * 1024
TEXT_MAX = 20000
REPEAT_MIN, REPEAT_KEEP = 10, 5   # a run of 10+ look-alike siblings is a list: probe 5 of it


class Refused(Exception):
    """The mapper will not run against this target."""


# --- policy (pure, unit-tested) ---------------------------------------------------------------------------

def host_denied(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    labels = host.split(".")
    for i in range(len(labels) - 1):
        if hashlib.sha256(".".join(labels[i:]).encode()).hexdigest() in DENIED_HOST_HASHES:
            return True
    return any(host == s or host.endswith("." + s) for s in DENIED_HOST_SUFFIXES)


def is_loopback(host: str) -> bool:
    host = (host or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def origin_of(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def normalize(url: str, base: str = None) -> str:
    """Absolute URL without fragment; '' for non-http(s)."""
    full = urllib.parse.urljoin(base, url) if base else url
    p = urllib.parse.urlsplit(full)
    if p.scheme not in ("http", "https"):
        return ""
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), p.path or "/", p.query, ""))


def label_denied(label: str) -> bool:
    return bool(DENY_LABEL.search(label or ""))


def url_denied(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    return bool(DENY_URL.search(p.path + ("?" + p.query if p.query else "")))


class Robots:
    """RFC 9309: product-token groups (else '*'), longest match wins, allow wins ties, '*' and '$' patterns."""

    def __init__(self, text: str = "", allow_all: bool = False, disallow_all: bool = False):
        self.allow_all, self.disallow_all = allow_all, disallow_all
        self.rules, self.crawl_delay, self.sitemaps = [], None, []
        if text:
            self._parse(text[:ROBOTS_MAX])

    def _parse(self, text: str):
        groups, current, in_rules = [], None, False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (s.strip() for s in line.split(":", 1))
            key = key.lower()
            if key == "sitemap":
                self.sitemaps.append(value)
                continue
            if key == "user-agent":
                if current is None or in_rules:
                    current = {"agents": [], "rules": [], "delay": None}
                    groups.append(current)
                    in_rules = False
                current["agents"].append(value.lower())
            elif current is not None and key in ("allow", "disallow"):
                in_rules = True
                if value or key == "allow":
                    current["rules"].append((key == "allow", value))
            elif current is not None and key == "crawl-delay":
                in_rules = True
                try:
                    current["delay"] = float(value)
                except ValueError:
                    pass
        mine = [g for g in groups if ROBOTS_TOKEN in g["agents"]]
        chosen = mine or [g for g in groups if "*" in g["agents"]]
        for g in chosen:
            self.rules.extend(g["rules"])
            if g["delay"] is not None:
                self.crawl_delay = max(self.crawl_delay or 0.0, g["delay"])

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        rx = "^" + ".*".join(re.escape(part) for part in body.split("*")) + ("$" if anchored else "")
        return re.match(rx, path) is not None

    def allowed(self, url: str) -> bool:
        if self.disallow_all:
            return False
        if self.allow_all:
            return True
        p = urllib.parse.urlsplit(url)
        path = (p.path or "/") + ("?" + p.query if p.query else "")
        if p.path == "/robots.txt":
            return True
        best = None
        for allow, pattern in self.rules:
            if self._matches(pattern, path):
                key = (len(pattern), allow)
                if best is None or key > best:
                    best = key
        return True if best is None else best[1]


class RateLimiter:
    def __init__(self, interval: float):
        self.interval, self.last = max(0.0, interval), 0.0

    def wait(self):
        gap = self.last + self.interval - time.monotonic()
        if gap > 0:
            time.sleep(gap)
        self.last = time.monotonic()


def decide(url: str, method: str, origin: str, robots: Robots) -> str:
    """'' when a paused browser request may go out, else the reason it is failed."""
    p = urllib.parse.urlsplit(url)
    if p.scheme in ("data", "blob", "about"):
        return ""
    if host_denied(p.hostname or ""):
        return "denied host"
    if method.upper() not in READ_METHODS:
        return f"write method {method.upper()}"
    if p.scheme not in ("http", "https"):
        return f"scheme {p.scheme}"
    if origin_of(url) != origin:
        return "cross-origin"
    if url_denied(url):
        return "destructive URL pattern"
    if not robots.allowed(url):
        return "robots.txt"
    return ""


def check_target(url: str, terms_ok: bool) -> str:
    """The normalized start URL, or Refused."""
    start = normalize(url)
    if not start:
        raise Refused("only http(s) URLs can be mapped")
    host = urllib.parse.urlsplit(start).hostname or ""
    if host_denied(host):
        raise Refused(f"{host} is on the hard deny-list; it is never mapped or probed")
    if not is_loopback(host) and not terms_ok:
        raise Refused("map only sites you own or whose terms allow it; re-run with --terms-ok to confirm")
    return start


# --- page-side scripts ------------------------------------------------------------------------------------

GUARD_JS = r"""
(() => {
  const log = (kind, detail) => { try { (window.__vsm = window.__vsm || []).push([kind, String(detail)]); } catch (e) {} };
  const Blocked = function (url) { log('websocket', url); throw new Error('crawlhound: WebSocket blocked'); };
  try { window.WebSocket = Blocked; } catch (e) {}
  try { navigator.sendBeacon = (url) => { log('beacon', url); return false; }; } catch (e) {}
  try { window.open = (url) => { log('window.open', url); return null; }; } catch (e) {}
  for (const name of ['pushState', 'replaceState']) {
    const orig = history[name];
    history[name] = function (s, t, url) { if (url != null) log(name, new URL(url, location.href).href); return orig.apply(this, arguments); };
  }
  try { HTMLFormElement.prototype.submit = function () { log('form.submit', this.action); }; } catch (e) {}
  try { HTMLFormElement.prototype.requestSubmit = function () { log('form.submit', this.action); }; } catch (e) {}
  document.addEventListener('submit', (ev) => { ev.preventDefault(); ev.stopImmediatePropagation(); log('form.submit', ev.target && ev.target.action); }, true);
})();
"""

SNAPSHOT_JS = r"""
(() => {
  const DENY = new RegExp(%DENY%, 'i');
  const vis = (el) => { if (!el.getClientRects().length) return false; const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none'; };
  const label = (el) => (el.getAttribute('aria-label') || el.innerText || el.value || el.title || el.getAttribute('alt') || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const sel = (el) => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
      if (n.id && document.querySelectorAll('#' + CSS.escape(n.id)).length === 1) { parts.unshift('#' + CSS.escape(n.id)); break; }
      let i = 1; for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) i++;
      parts.unshift(n.tagName.toLowerCase() + ':nth-of-type(' + i + ')');
    }
    return parts.join(' > ');
  };
  const roleOf = (el) => el.getAttribute('role') || el.tagName.toLowerCase();
  const strip = (s) => s.replace(/[0-9]+/g, '#');
  const sig = [];
  const interactive = 'a,button,input,select,textarea,summary,details,[role],[aria-expanded],[aria-selected],[tabindex],dialog,h1,h2,h3';
  for (const el of document.querySelectorAll(interactive)) {
    if (!vis(el)) continue;
    sig.push([roleOf(el), strip(label(el)).slice(0, 40), el.getAttribute('aria-expanded'), el.getAttribute('aria-selected'),
              el.getAttribute('aria-pressed'), el.open === true ? 'open' : ''].join('|'));
  }
  const counts = {};
  for (const el of document.querySelectorAll('li,tr,p,img,section,article,fieldset,iframe')) if (vis(el)) counts[el.tagName] = (counts[el.tagName] || 0) + 1;
  for (const [t, n] of Object.entries(counts)) sig.push('count|' + t + '|' + n);
  const candidates = [];
  const seen = new Set();
  const cq = 'button,summary,[role=button],[role=tab],[role=menuitem],[role=treeitem],[role=switch],[role=link],[aria-controls],[aria-expanded],[onclick],a';
  for (const el of document.querySelectorAll(cq)) {
    if (seen.has(el) || !vis(el)) continue; seen.add(el);
    const tag = el.tagName.toLowerCase();
    const href = tag === 'a' ? (el.getAttribute('href') || '') : '';
    if (tag === 'a' && href && !href.startsWith('#') && !href.toLowerCase().startsWith('javascript:') && el.getAttribute('role') !== 'button' && el.getAttribute('role') !== 'tab') continue;
    const lbl = label(el);
    let skip = '';
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') skip = 'disabled';
    else if (el.closest('form')) skip = 'inside a form';
    else if (tag === 'input' && /^(submit|file|image|reset)$/i.test(el.type)) skip = 'submit/file input';
    else if (DENY.test(lbl) || DENY.test(el.getAttribute('name') || '') || DENY.test(el.id || '')) skip = 'destructive label';
    const p = el.parentElement;
    candidates.push({selector: sel(el), label: lbl, role: roleOf(el), group: (p ? sel(p) : '') + '>' + tag + '.' + (el.getAttribute('role') || '') + '.' + (el.className || ''), skip});
  }
  const links = [];
  for (const a of document.querySelectorAll('a[href],area[href]')) links.push({href: a.href, label: label(a)});
  const forms = [];
  for (const f of document.forms) {
    const fields = [];
    for (const el of f.elements) {
      const lab = (el.labels && el.labels[0] && el.labels[0].innerText.trim()) || el.getAttribute('aria-label') || el.placeholder || '';
      fields.push({tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '', label: lab.slice(0, 80), required: !!el.required});
    }
    forms.push({action: f.getAttribute('action') ? f.action : '', method: (f.getAttribute('method') || 'get').toLowerCase(), selector: sel(f), fields});
  }
  const assets = new Set();
  const add = (u) => { if (u && !u.startsWith('data:')) assets.add(u); };
  document.querySelectorAll('script[src]').forEach((e) => add(e.src));
  document.querySelectorAll('link[href]').forEach((e) => add(e.href));
  document.querySelectorAll('img[src],video[src],audio[src],source[src],iframe[src],embed[src],track[src]').forEach((e) => add(e.src));
  document.querySelectorAll('[srcset]').forEach((e) => e.srcset.split(',').forEach((c) => add(new URL(c.trim().split(/\s+/)[0], location.href).href)));
  const headings = [...document.querySelectorAll('h1,h2,h3')].filter((h) => vis(h) && label(h)).map((h) => h.tagName.toLowerCase() + ': ' + label(h));
  const dialogs = [...document.querySelectorAll('dialog[open],[role=dialog],[aria-modal=true]')].filter(vis).map(label);
  return {url: location.href, title: document.title, sig, candidates, links, forms, assets: [...assets], headings, dialogs,
          text: (document.body ? document.body.innerText : '').slice(0, %TEXTMAX%), events: window.__vsm || []};
})()
""".replace("%DENY%", json.dumps(DENY_LABEL.pattern)).replace("%TEXTMAX%", str(TEXT_MAX))

CLICK_JS = r"""
((sel) => {
  const el = document.querySelector(sel);
  if (!el) return 'missing';
  const lbl = (el.getAttribute('aria-label') || el.innerText || el.value || el.title || '').replace(/\s+/g, ' ').trim();
  if (new RegExp(%DENY%, 'i').test(lbl)) return 'destructive label';
  if (el.closest('form')) return 'inside a form';
  el.scrollIntoView({block: 'center'});
  el.click();
  return 'clicked';
})(%SEL%)
""".replace("%DENY%", json.dumps(DENY_LABEL.pattern))


def fingerprint(snap: dict) -> str:
    """URL (path, query, hash) + structural signature of what is visible and interactive."""
    p = urllib.parse.urlsplit(snap.get("url", ""))
    key = json.dumps([p.path, p.query, p.fragment, sorted(snap.get("sig", [])), sorted(snap.get("dialogs", []))])
    return hashlib.sha256(key.encode()).hexdigest()[:12]


# --- the crawl --------------------------------------------------------------------------------------------

class Mapper:
    def __init__(self, start: str, max_pages=100, max_states=300, depth=3, clicks_per_page=80, delay=None,
                 click=True, browser_factory=None, log=print, extractor=None, include=None, scroll=0,
                 load_media=False):
        self.cdp = _cdp
        self.start, self.origin = start, origin_of(start)
        self.host = urllib.parse.urlsplit(start).hostname or ""
        self.max_pages, self.max_states, self.depth = max_pages, max_states, depth
        self.clicks_per_page, self.click = clicks_per_page, click
        self.requested_delay = delay
        self.browser_factory = browser_factory
        self.log = log
        self.robots = Robots(allow_all=True)
        self.pages, self.states, self.edges, self.ledger = {}, {}, [], []
        self.routes, self.blocked, self.api, self.assets = {}, [], {}, {}
        self.external_links, self.forms, self.page_events = set(), {}, []
        self.sitemap_urls, self.statuses, self.notes = [], {}, []
        self.queue, self.queued, self.accepted = [], set(), 0
        self.inflight, self.loaded, self.nav_docs = set(), False, []
        self.extractor, self.scroll, self.load_media = extractor, scroll, load_media
        self.include = re.compile(include) if include else None
        self.media_skipped = 0

    # robots, rate, sitemap (urllib; same UA as the browser)
    def _get(self, url: str, cap: int):
        self.rate.wait()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:  # http(s) only, same origin, checked by caller  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            if origin_of(resp.geturl()) != self.origin:
                raise urllib.error.URLError("redirected off-origin")
            return resp.status, resp.read(cap + 1)[:cap].decode("utf-8", "replace")

    def load_robots(self):
        url = self.origin + "/robots.txt"
        try:
            _, text = self._get(url, ROBOTS_MAX)
            self.robots = Robots(text)
            self.notes.append("robots.txt: found and honoured")
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                self.robots = Robots(allow_all=True)
                self.notes.append(f"robots.txt: HTTP {exc.code}, treated as allow-all (RFC 9309)")
            else:
                self.robots = Robots(disallow_all=True)
                self.notes.append(f"robots.txt: HTTP {exc.code}, treated as disallow-all (RFC 9309)")
        except (urllib.error.URLError, OSError) as exc:
            self.robots = Robots(disallow_all=True)
            self.notes.append(f"robots.txt: unreachable ({exc}), treated as disallow-all (RFC 9309)")

    def load_sitemaps(self):
        todo = [s for s in self.robots.sitemaps if origin_of(s) == self.origin] or [self.origin + "/sitemap.xml"]
        seen = set()
        while todo and len(seen) < 10:
            url = todo.pop(0)
            if url in seen or decide(url, "GET", self.origin, self.robots):
                continue
            seen.add(url)
            try:
                _, text = self._get(url, SITEMAP_MAX)
            except (urllib.error.URLError, OSError):
                continue
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                self.notes.append(f"sitemap {url}: DOCTYPE/ENTITY refused")
                continue
            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                continue
            for loc in root.iter():
                if loc.tag.endswith("loc") and loc.text:
                    u = normalize(loc.text.strip())
                    if not u:
                        continue
                    if root.tag.endswith("sitemapindex"):
                        if origin_of(u) == self.origin:
                            todo.append(u)
                    else:
                        self.sitemap_urls.append(u)

    # CDP events
    def on_event(self, method: str, params: dict):
        if method == "Fetch.requestPaused":
            req = params.get("request", {})
            url, verb, rtype = req.get("url", ""), req.get("method", "GET"), params.get("resourceType", "")
            reason = decide(url, verb, self.origin, self.robots)
            if not reason and rtype in MEDIA_TYPES and not self.load_media:
                self.media_skipped += 1
                self.browser.send("Fetch.failRequest", {"requestId": params["requestId"], "errorReason": "BlockedByClient"})
                self.assets.setdefault(url, rtype)
                return
            if not reason and urllib.parse.urlsplit(url).scheme in ("http", "https"):
                self.rate.wait()
            if reason:
                self.blocked.append({"method": verb, "url": url, "type": rtype, "reason": reason})
                self.browser.send("Fetch.failRequest", {"requestId": params["requestId"], "errorReason": "BlockedByClient"})
            else:
                self.browser.send("Fetch.continueRequest", {"requestId": params["requestId"]})
            if rtype in ASSET_TYPES:
                self.assets.setdefault(url, rtype)
            elif rtype in API_TYPES:
                self.api.setdefault(f"{verb} {url}", "blocked: " + reason if reason else "allowed")
            elif rtype == "Document" and params.get("frameId") and not reason:
                self.nav_docs.append(url)
        elif method == "Network.requestWillBeSent":
            self.inflight.add(params.get("requestId"))
        elif method in ("Network.loadingFinished", "Network.loadingFailed"):
            self.inflight.discard(params.get("requestId"))
        elif method == "Network.responseReceived" and params.get("type") == "Document":
            resp = params.get("response", {})
            self.statuses[normalize(resp.get("url", ""))] = resp.get("status")
        elif method == "Page.loadEventFired":
            self.loaded = True
        elif method == "Page.javascriptDialogOpening":
            self.page_events.append(["dialog dismissed", params.get("message", "")[:120]])
            self.browser.send("Page.handleJavaScriptDialog", {"accept": False})
        elif method == "Page.fileChooserOpened":
            self.page_events.append(["file chooser left unanswered", ""])
        elif method == "Page.navigatedWithinDocument":
            url = params.get("url", "")
            if origin_of(url) == self.origin:
                self.routes.setdefault(url, "same-document")
        elif method == "Page.downloadWillBegin":
            self.page_events.append(["download denied", params.get("url", "")])

    def settle(self, max_wait=8.0, quiet=0.4):
        deadline, quiet_since = time.monotonic() + max_wait, None
        while time.monotonic() < deadline:
            self.browser.pump(0.1)
            if self.inflight:
                quiet_since = None
                continue
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= quiet:
                return

    def goto(self, url: str) -> bool:
        self.loaded, self.inflight = False, set()
        res = self.browser.call("Page.navigate", {"url": url})
        if res.get("errorText"):
            return False
        deadline = time.monotonic() + 20
        while not self.loaded and time.monotonic() < deadline:
            self.browser.pump(0.1)
        self.settle()
        return True

    def scroll_load(self):
        """Scroll to the bottom until the page stops growing, the URL changes or the step/time cap is hit."""
        start_url, deadline, still = self.browser.evaluate("location.href"), time.monotonic() + SCROLL_WAIT, 0
        last = self.browser.evaluate("document.documentElement.scrollHeight")
        for _ in range(min(self.scroll, SCROLL_STEPS)):
            self.browser.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            self.settle(max_wait=4.0, quiet=0.6)
            height = self.browser.evaluate("document.documentElement.scrollHeight")
            if self.browser.evaluate("location.href") != start_url or time.monotonic() > deadline:
                break
            still = still + 1 if height == last else 0
            if still >= 2:
                break
            last = height

    def snapshot(self) -> dict:
        return self.browser.evaluate(SNAPSHOT_JS)

    def reach(self, url: str, path: list) -> bool:
        if not self.goto(url):
            return False
        for selector in path:
            if self.browser.evaluate(CLICK_JS.replace("%SEL%", json.dumps(selector))) != "clicked":
                return False
            self.settle()
        return True

    def enqueue(self, url: str, via: str):
        u = normalize(url)
        if not u or u in self.queued:
            return
        if origin_of(u) != self.origin:
            self.external_links.add(u)
            return
        reason = decide(u, "GET", self.origin, self.robots)
        if not reason and self.include and u != self.start and not self.include.search(u):
            reason = "outside --include"
        if reason:
            self.ledger.append({"page": u, "candidate": via, "outcome": "skipped", "reason": reason})
            self.queued.add(u)
            return
        if self.accepted >= self.max_pages:  # skipped URLs never count toward the cap
            self.ledger.append({"page": u, "candidate": via, "outcome": "skipped", "reason": "page cap"})
            return
        self.queued.add(u)
        self.queue.append(u)
        self.accepted += 1

    def record_state(self, page: str, snap: dict, path: list, depth: int) -> tuple:
        fp = fingerprint(snap)
        if fp in self.states:
            return fp, False
        self.states[fp] = {"page": page, "url": snap["url"], "title": snap["title"], "depth": depth, "path": path,
                           "headings": snap["headings"], "dialogs": snap["dialogs"], "text": snap["text"]}
        if self.extractor and self.extractor.wants(snap["url"]):
            try:
                found = self.extractor.add(self.browser.evaluate(self.extractor.js), snap["url"], fp)
                self.states[fp]["items"] = found
            except self.cdp.CdpError as exc:
                self.notes.append(f"extract failed on {snap['url']}: {exc}")
        for form in snap["forms"]:
            self.forms.setdefault((page, form["selector"]), dict(form, page=page, state=fp))
        for asset in snap["assets"]:
            self.assets.setdefault(asset, "referenced")
        for link in snap["links"]:
            self.enqueue(link["href"], f"link: {link['label']}")
        for kind, detail in snap["events"]:
            if kind in ("pushState", "replaceState") and origin_of(detail) == self.origin:
                self.routes.setdefault(detail, kind)
            else:
                self.page_events.append([kind, detail])
        return fp, True

    def map_page(self, url: str):
        if not self.goto(url):
            self.pages[url] = {"status": "failed to load"}
            return
        if self.scroll:
            self.scroll_load()
        snap = self.snapshot()
        landed = normalize(snap["url"])
        if landed != url and origin_of(landed) != self.origin:
            self.pages[url] = {"status": "redirected off-origin", "to": landed}
            return
        root, _ = self.record_state(url, snap, [], 0)
        self.pages[url] = {"status": self.statuses.get(url) or self.statuses.get(landed), "title": snap["title"],
                           "root_state": root, "text_chars": len(snap["text"])}
        if not self.click:
            return
        budget = self.clicks_per_page
        frontier = [(root, [], 0, snap, set())]
        while frontier:
            fp, path, depth, snap, parent_keys = frontier.pop(0)
            groups, sizes = {}, {}
            for c in snap["candidates"]:
                sizes[c["group"]] = sizes.get(c["group"], 0) + 1
            keys = {(c["selector"], c["label"]) for c in snap["candidates"]}
            for cand in snap["candidates"]:
                key = (cand["selector"], cand["label"])
                entry = {"page": url, "state": fp, "candidate": f"{cand['role']} '{cand['label']}'",
                         "selector": cand["selector"]}
                if cand["skip"]:
                    self.ledger.append(dict(entry, outcome="skipped", reason=cand["skip"]))
                    continue
                if key in parent_keys:
                    self.ledger.append(dict(entry, outcome="skipped", reason="already probed in parent state"))
                    continue
                groups.setdefault(cand["group"], 0)
                groups[cand["group"]] += 1
                if sizes[cand["group"]] >= REPEAT_MIN and groups[cand["group"]] > REPEAT_KEEP:
                    self.ledger.append(dict(entry, outcome="skipped",
                                            reason=f"repeated sibling ({REPEAT_KEEP} of {sizes[cand['group']]} probed)"))
                    continue
                if depth >= self.depth:
                    self.ledger.append(dict(entry, outcome="skipped", reason=f"depth cap {self.depth}"))
                    continue
                if budget <= 0 or len(self.states) >= self.max_states:
                    self.ledger.append(dict(entry, outcome="skipped", reason="click budget / state cap"))
                    continue
                budget -= 1
                self.ledger.append(self.probe(url, fp, path, depth, cand, entry, frontier, keys))

    def probe(self, url, fp, path, depth, cand, entry, frontier, parent_keys):
        if not self.reach(url, path):
            return dict(entry, outcome="unreachable", reason="could not replay the path to this state")
        self.nav_docs = []
        before = self.browser.evaluate("location.href")
        result = self.browser.evaluate(CLICK_JS.replace("%SEL%", json.dumps(cand["selector"])))
        if result != "clicked":
            return dict(entry, outcome="skipped", reason=result)
        self.settle()
        if self.nav_docs:
            target = normalize(self.nav_docs[-1])
            self.enqueue(target, f"click: {cand['label']}")
            self.edges.append({"from": fp, "action": entry["candidate"], "to": "page " + target})
            return dict(entry, outcome="navigates", reason=target)
        snap = self.snapshot()
        new_fp, is_new = self.record_state(url, snap, path + [cand["selector"]], depth + 1)
        if snap["url"] != before and origin_of(snap["url"]) == self.origin:
            self.routes.setdefault(snap["url"], "same-document")
        if new_fp == fp:
            return dict(entry, outcome="no change", reason="")
        self.edges.append({"from": fp, "action": entry["candidate"], "to": new_fp})
        if is_new:
            frontier.append((new_fp, path + [cand["selector"]], depth + 1, snap, set(parent_keys)))
            return dict(entry, outcome="new state", reason=new_fp)
        return dict(entry, outcome="known state", reason=new_fp)

    def run(self) -> dict:
        loop = is_loopback(self.host)
        self.rate = RateLimiter(1.0)
        self.load_robots()
        floor = 0.0 if loop else max(1.0, self.robots.crawl_delay or 0.0)
        wanted = self.requested_delay if self.requested_delay is not None else max(1.0, self.robots.crawl_delay or 0.0)
        self.rate = RateLimiter(max(floor, wanted))
        self.notes.append(f"rate: one request per {self.rate.interval:g} s")
        if not self.robots.allowed(self.start):
            raise Refused("robots.txt disallows the start URL")
        self.load_sitemaps()
        chrome = self.cdp.find_chrome()
        if not chrome and not self.browser_factory:
            raise Refused("no installed Chrome or Chromium found (the mapper never downloads one)")
        factory = self.browser_factory or (lambda ev: self.cdp.Browser(chrome, ev))
        self.browser = factory(self.on_event)
        try:
            b = self.browser
            b.call("Page.enable")
            b.call("Network.enable")
            b.call("Network.setBypassServiceWorker", {"bypass": True})
            b.call("Network.setUserAgentOverride", {"userAgent": USER_AGENT})
            b.call("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
            b.call("Page.setInterceptFileChooserDialog", {"enabled": True})
            b.call("Page.addScriptToEvaluateOnNewDocument", {"source": GUARD_JS})
            try:
                b.call("Browser.setDownloadBehavior", {"behavior": "deny"}, browser_level=True)
            except self.cdp.CdpError:
                self.notes.append("downloads: Browser.setDownloadBehavior unavailable; Fetch guard still applies")
            self.enqueue(self.start, "start")
            for u in self.sitemap_urls:
                self.enqueue(u, "sitemap.xml")
            while self.queue:
                url = self.queue.pop(0)
                self.log(f"map {url}")
                self.map_page(url)
        finally:
            self.browser.close()
        return self.report()

    def report(self) -> dict:
        budget_skips = [e for e in self.ledger if e.get("reason") in ("click budget / state cap", "page cap")
                        or str(e.get("reason", "")).startswith("depth cap") or e["outcome"] == "unreachable"]
        reached = set(self.pages)
        sitemap = set(self.sitemap_urls)
        return {
            "start": self.start, "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "user_agent": USER_AGENT,
            "policy": {"read_methods": list(READ_METHODS), "same_origin": True, "denied_hosts": "private (hashed)",
                       "denied_host_suffixes": list(DENIED_HOST_SUFFIXES), "max_pages": self.max_pages,
                       "max_states": self.max_states, "depth": self.depth, "clicks_per_page": self.clicks_per_page,
                       "rate_interval_s": self.rate.interval, "click_probing": self.click,
                       "include": self.include.pattern if self.include else None, "scroll_steps": self.scroll,
                       "media_loaded": self.load_media},
            "notes": self.notes,
            "coverage": {"verdict": "complete for this policy" if not budget_skips else "partial: caps reached",
                         "pages": len(self.pages), "states": len(self.states), "routes": len(self.routes),
                         "candidates_probed": sum(1 for e in self.ledger if e["outcome"] not in ("skipped",)),
                         "candidates_skipped": sum(1 for e in self.ledger if e["outcome"] == "skipped"),
                         "cap_or_unreachable": len(budget_skips),
                         "sitemap_not_reached": sorted(sitemap - reached),
                         "reached_not_in_sitemap": sorted(reached - sitemap) if sitemap else []},
            "pages": self.pages, "states": self.states, "edges": self.edges, "routes": self.routes,
            "forms": list(self.forms.values()), "assets": self.assets, "api": self.api,
            "blocked_requests": self.blocked, "page_events": self.page_events,
            "external_links": sorted(self.external_links), "ledger": self.ledger,
            "media_skipped": self.media_skipped,
            "extract": self.extractor.summary() if self.extractor else None,
        }


def to_markdown(rep: dict) -> str:
    cov = rep["coverage"]
    out = [f"# Site map: {rep['start']}", ""]
    if rep.get("extract"):
        ex = rep["extract"]
        out += [f"Data: **{ex['items']} items** ({ex['schema']}, {ex['mode']} mode) from {ex['states_matched']} "
                f"states; field fill: " + ", ".join(f"{k} {v}" for k, v in ex["field_fill"].items()) + ". "
                "Items are in items.jsonl.", ""]
    out += [ f"Generated {rep['generated']}. Coverage: **{cov['verdict']}** "
           f"({cov['pages']} pages, {cov['states']} states, {cov['routes']} routes, {cov['candidates_probed']} "
           f"clicks probed, {cov['candidates_skipped']} skipped with reason).", ""]
    out += ["## Policy", ""] + [f"- {n}" for n in rep["notes"]] + [
        f"- read methods only: {', '.join(rep['policy']['read_methods'])}; same-origin; caps: "
        f"{rep['policy']['max_pages']} pages, {rep['policy']['max_states']} states, depth {rep['policy']['depth']}", ""]
    out += ["## Pages", "", "| URL | Status | Title |", "|---|---|---|"]
    out += [f"| {u} | {p.get('status')} | {p.get('title', '')} |" for u, p in sorted(rep["pages"].items())]
    out += ["", "## States", "", "| State | URL | Depth | Headings | Dialogs |", "|---|---|---|---|---|"]
    out += [f"| {k} | {s['url']} | {s['depth']} | {'; '.join(s['headings'][:4])} | {'; '.join(s['dialogs'])} |"
            for k, s in rep["states"].items()]
    out += ["", "## Transitions", ""] + [f"- {e['from']} --{e['action']}--> {e['to']}" for e in rep["edges"]]
    out += ["", "## Routes", ""] + [f"- {u} ({k})" for u, k in sorted(rep["routes"].items())]
    out += ["", "## Forms (fields only, never submitted)", ""]
    for f in rep["forms"]:
        fields = ", ".join(f"{x['name'] or x['label'] or x['tag']}:{x['type']}" + ("*" if x["required"] else "")
                           for x in f["fields"])
        out.append(f"- {f['page']} {f['method'].upper()} {f['action'] or '(no action)'}: {fields}")
    out += ["", "## Blocked requests (never left the browser)", ""]
    out += [f"- {b['method']} {b['url']} ({b['reason']})" for b in rep["blocked_requests"]] or ["- none"]
    out += ["", "## GET API endpoints seen", ""] + [f"- {k}: {v}" for k, v in sorted(rep["api"].items())]
    out += ["", f"## Assets ({len(rep['assets'])}, listed only)", ""] + [f"- {u}" for u in sorted(rep["assets"])]
    out += ["", "## Click ledger", "", "| Page | State | Candidate | Outcome | Reason |", "|---|---|---|---|---|"]
    out += [f"| {e['page']} | {e.get('state', '')} | {e['candidate']} | {e['outcome']} | {e.get('reason', '')} |"
            for e in rep["ledger"]]
    if cov["sitemap_not_reached"]:
        out += ["", "## In sitemap.xml but not reached", ""] + [f"- {u}" for u in cov["sitemap_not_reached"]]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="spider.py", description="Crawl a site read-only: map it and pull the data you choose.")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--terms-ok", action="store_true", help="you own this site or its terms allow crawling")
    ap.add_argument("--out", help="output folder (default ./site-maps/<host>-<time>)")
    ap.add_argument("--extract", metavar="SCHEMA", help="preset name or JSON schema file: the data to pull")
    ap.add_argument("--include", metavar="REGEX", help="only follow same-origin URLs matching this regex")
    ap.add_argument("--scroll", type=int, default=0, metavar="N", help=f"scroll-load each page up to N steps (max {SCROLL_STEPS})")
    ap.add_argument("--load-media", action="store_true", help="also load images, fonts and media (skipped by default)")
    ap.add_argument("--list-presets", action="store_true", help="print the preset names and exit")
    ap.add_argument("--check-schema", metavar="SCHEMA", help="validate a preset or schema file offline and exit")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--max-states", type=int, default=300)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--clicks-per-page", type=int, default=80)
    ap.add_argument("--delay", type=float, help="seconds between requests (never below 1 s or Crawl-delay off loopback)")
    ap.add_argument("--no-click", action="store_true", help="links only, no click-probing")
    args = ap.parse_args(argv)
    if args.list_presets:
        print("\n".join(_extract.presets()))
        return 0
    if args.check_schema:
        try:
            schema = _extract.load(args.check_schema)
        except _extract.SchemaError as exc:
            print(f"Schema error: {exc}", file=sys.stderr)
            return 2
        print(f"OK {schema['name']} ({schema['mode']}): fields {', '.join(schema['fields']) or '-'}")
        return 0
    if not args.url:
        ap.error("a start URL is required")
    try:
        extractor = _extract.Extractor(_extract.load(args.extract)) if args.extract else None
        if args.include:
            re.compile(args.include)
        start = check_target(args.url, args.terms_ok)
        mapper = Mapper(start, args.max_pages, args.max_states, args.depth, args.clicks_per_page, args.delay,
                        click=not args.no_click, log=lambda m: print(m, file=sys.stderr), extractor=extractor,
                        include=args.include, scroll=args.scroll, load_media=args.load_media)
        rep = mapper.run()
    except _extract.SchemaError as exc:
        print(f"Schema error: {exc}", file=sys.stderr)
        return 2
    except re.error as exc:
        print(f"Bad --include regex: {exc}", file=sys.stderr)
        return 2
    except Refused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    host = urllib.parse.urlsplit(start).netloc.replace(":", "_")
    out = Path(args.out) if args.out else Path.cwd() / "site-maps" / f"{host}-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "map.json").write_text(json.dumps(rep, indent=1))
    (out / "map.md").write_text(to_markdown(rep))
    if extractor:
        with open(out / "items.jsonl", "w") as fh:
            for item in extractor.items:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"{len(extractor.items)} items -> {out / 'items.jsonl'}")
    cov = rep["coverage"]
    print(f"{cov['verdict']}: {cov['pages']} pages, {cov['states']} states, {cov['routes']} routes, "
          f"{len(rep['blocked_requests'])} requests blocked")
    print(f"Report: {out / 'map.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
