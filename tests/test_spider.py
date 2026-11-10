"""crawlhound spider: policy units, schema extraction, WebSocket framing, and an end-to-end crawl of a localhost fixture.

The end-to-end test needs an installed Chrome; it is skipped when none is found."""
import hashlib
import http.server
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "crawlhound" / "scripts"))
import cdp  # noqa: E402
import extract  # noqa: E402
import spider as sm  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "site"


class HostPolicy(unittest.TestCase):
    def setUp(self):
        self._saved = sm.DENIED_HOST_HASHES
        sm.DENIED_HOST_HASHES = frozenset({hashlib.sha256(b"blocked.example").hexdigest()})

    def tearDown(self):
        sm.DENIED_HOST_HASHES = self._saved

    def test_hard_deny_list(self):
        for host in ("blocked.example", "www.blocked.example", "abc.supabase.co",
                     "x.supabase.in", "supabase.co", "BLOCKED.EXAMPLE."):
            self.assertTrue(sm.host_denied(host), host)
        for host in ("example.com", "notsupabase.co.uk", "127.0.0.1", "blocked.example.org", "notblocked.example"):
            self.assertFalse(sm.host_denied(host), host)

    def test_shipped_deny_list_is_hashed_only(self):
        self.assertTrue(self._saved and all(len(h) == 64 for h in self._saved))

    def test_start_url_refused_for_denied_hosts_even_with_terms_ok(self):
        for url in ("https://blocked.example/", "https://app.blocked.example/x",
                    "https://abcd.supabase.co/rest/v1/"):
            with self.assertRaises(sm.Refused):
                sm.check_target(url, terms_ok=True)

    def test_non_loopback_needs_terms_ok(self):
        with self.assertRaises(sm.Refused):
            sm.check_target("https://example.com/", terms_ok=False)
        self.assertEqual(sm.check_target("https://Example.com/#x", terms_ok=True), "https://example.com/")
        self.assertEqual(sm.check_target("http://127.0.0.1:8000", terms_ok=False), "http://127.0.0.1:8000/")
        with self.assertRaises(sm.Refused):
            sm.check_target("ftp://example.com/", terms_ok=True)

    def test_loopback(self):
        for host in ("localhost", "127.0.0.1", "[::1]", "app.localhost"):
            self.assertTrue(sm.is_loopback(host), host)
        self.assertFalse(sm.is_loopback("192.168.1.2"))


class RequestGuard(unittest.TestCase):
    origin = "http://127.0.0.1:9"

    def setUp(self):
        self.robots = sm.Robots("User-agent: *\nDisallow: /private/\n")

    def test_only_get_and_head_leave(self):
        for verb in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            self.assertTrue(sm.decide(self.origin + "/api", verb, self.origin, self.robots).startswith("write"))
        self.assertEqual(sm.decide(self.origin + "/api", "GET", self.origin, self.robots), "")
        self.assertEqual(sm.decide(self.origin + "/api", "head", self.origin, self.robots), "")

    def test_scope_robots_and_destructive_urls(self):
        self.assertEqual(sm.decide("https://cdn.example.com/a.js", "GET", self.origin, self.robots), "cross-origin")
        self.assertEqual(sm.decide(self.origin + "/private/x", "GET", self.origin, self.robots), "robots.txt")
        self.assertEqual(sm.decide(self.origin + "/logout", "GET", self.origin, self.robots), "destructive URL pattern")
        self.assertEqual(sm.decide(self.origin + "/items?delete=1", "GET", self.origin, self.robots),
                         "destructive URL pattern")
        self.assertEqual(sm.decide("https://x.supabase.co/rest", "GET", self.origin, self.robots), "denied host")
        self.assertEqual(sm.decide("data:image/png;base64,AA", "GET", self.origin, self.robots), "")

    def test_labels(self):
        for label in ("Submit", "Pay now", "Delete account", "Log out", "Sign out", "Book a call", "Save draft",
                      "Upload file", "Confirm", "Send"):
            self.assertTrue(sm.label_denied(label), label)
        for label in ("Reports", "Open help", "Load more", "Weekly", "Settings", "Bookmarks"):
            self.assertFalse(sm.label_denied(label), label)


class RobotsRfc9309(unittest.TestCase):
    def test_longest_match_and_allow_tie(self):
        r = sm.Robots("User-agent: *\nDisallow: /a\nAllow: /a/b\nDisallow: /x\nAllow: /x\n")
        self.assertFalse(r.allowed("http://h/a/c"))
        self.assertTrue(r.allowed("http://h/a/b/c"))
        self.assertTrue(r.allowed("http://h/x"))  # tie -> allow
        self.assertTrue(r.allowed("http://h/other"))

    def test_wildcards_and_anchor(self):
        r = sm.Robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?session=\n")
        self.assertFalse(r.allowed("http://h/doc/a.pdf"))
        self.assertTrue(r.allowed("http://h/doc/a.pdf.html"))
        self.assertFalse(r.allowed("http://h/p?session=1"))

    def test_own_group_wins_and_crawl_delay(self):
        text = ("User-agent: *\nDisallow: /\nCrawl-delay: 2\n\n"
                "User-agent: crawlhound\nDisallow: /admin\nCrawl-delay: 5\nSitemap: http://h/s.xml\n")
        r = sm.Robots(text)
        self.assertTrue(r.allowed("http://h/page"))
        self.assertFalse(r.allowed("http://h/admin/x"))
        self.assertEqual(r.crawl_delay, 5.0)
        self.assertEqual(r.sitemaps, ["http://h/s.xml"])

    def test_empty_disallow_and_failure_modes(self):
        self.assertTrue(sm.Robots("User-agent: *\nDisallow:\n").allowed("http://h/x"))
        self.assertFalse(sm.Robots(disallow_all=True).allowed("http://h/x"))
        self.assertTrue(sm.Robots(allow_all=True).allowed("http://h/x"))


class WebSocketFraming(unittest.TestCase):
    def test_client_frames_are_masked_and_round_trip(self):
        for size in (0, 5, 125, 126, 65535, 65536):
            payload = os.urandom(size)
            frame = cdp.encode_frame(payload, mask=b"\x01\x02\x03\x04")
            self.assertTrue(frame[1] & 0x80, "client frames must be masked")
            frames, rest = cdp.decode_frames(frame)
            self.assertEqual(rest, b"")
            self.assertEqual(frames, [(1, 1, payload)])

    def test_partial_and_reframe(self):
        frame = cdp._reframe((1, 1, b"hello world"))
        frames, rest = cdp.decode_frames(frame[:5])
        self.assertEqual((frames, rest), ([], frame[:5]))
        frames, rest = cdp.decode_frames(frame + frame[:3])
        self.assertEqual(frames, [(1, 1, b"hello world")])
        self.assertEqual(rest, frame[:3])

    def test_devtools_endpoint_must_be_loopback(self):
        with self.assertRaises(cdp.CdpError):
            cdp.WebSocket("ws://example.com:9222/devtools/browser/x")  # refused: off loopback  # nosemgrep: javascript.lang.security.detect-insecure-websocket.detect-insecure-websocket


class Fingerprint(unittest.TestCase):
    def test_structure_and_url_count_order_does_not(self):
        b = {"url": "http://h/app?view=a", "sig": ["button|Inbox (#)|||", "h2|Inbox|||"], "dialogs": []}
        c = {"url": "http://h/app?view=a", "sig": ["button|Inbox (#)|true||", "h2|Inbox|||"], "dialogs": []}
        self.assertEqual(sm.fingerprint(b), sm.fingerprint(dict(b, sig=list(reversed(b["sig"])))))
        self.assertNotEqual(sm.fingerprint(b), sm.fingerprint(c))
        self.assertNotEqual(sm.fingerprint(b), sm.fingerprint(dict(b, url="http://h/app?view=b")))


class _Handler(http.server.SimpleHTTPRequestHandler):
    writes = []

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(FIXTURE), **kw)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0] == "/sitemap.xml":
            body = (FIXTURE / "sitemap.xml").read_text().replace(
                "{ORIGIN}", f"http://127.0.0.1:{self.server.server_address[1]}").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?")[0] == "/app.html":
            self.path = "/app.html"
        super().do_GET()

    def _write(self):
        _Handler.writes.append((self.command, self.path))
        self.send_response(405)
        self.end_headers()

    do_POST = do_PUT = do_PATCH = do_DELETE = _write


def serve_fixture():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@unittest.skipUnless(cdp.find_chrome(), "no installed Chrome")
class EndToEnd(unittest.TestCase):
    def test_maps_fixture_fully_and_read_only(self):
        _Handler.writes = []
        server = serve_fixture()
        try:
            start = f"http://127.0.0.1:{server.server_address[1]}/"
            rep = sm.Mapper(start, delay=0.0, log=lambda m: None).run()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(_Handler.writes, [], "the mapper must never send a write to the site")
        pages = {u.split("/", 3)[3] for u in rep["pages"]}
        self.assertEqual(pages, {"", "about.html", "app.html", "orphan.html", "shop.html", "feed.html"})
        blocked = {(b["method"], b["url"].split("/", 3)[3], b["reason"]) for b in rep["blocked_requests"]}
        self.assertIn(("POST", "api/track", "write method POST"), blocked)
        reasons = {e["candidate"]: e["reason"] for e in rep["ledger"] if e["outcome"] == "skipped"}
        self.assertEqual(reasons.get("button 'Delete account'"), "destructive label")
        self.assertEqual(reasons.get("button 'Submit'"), "inside a form")
        self.assertIn("robots.txt", {e["reason"] for e in rep["ledger"]})
        self.assertIn("destructive URL pattern", {e["reason"] for e in rep["ledger"]})
        headings = {h for s in rep["states"].values() for h in s["headings"]}
        for want in ("h2: Reports", "h3: Monthly numbers", "h2: Settings", "h2: Help", "h2: History"):
            self.assertIn(want, headings)
        routes = {u.split("/", 3)[3] for u in rep["routes"]}
        self.assertTrue({"app.html?view=reports", "app.html?view=settings"} <= routes, routes)
        self.assertEqual([f["method"] for f in rep["forms"]], ["post"])
        self.assertEqual([x["name"] for x in rep["forms"][0]["fields"] if x["name"]], ["name", "email"])
        self.assertIn("GET " + start + "api/items.json", rep["api"])
        self.assertEqual(rep["coverage"]["verdict"], "complete for this policy")
        self.assertEqual(rep["coverage"]["sitemap_not_reached"], [])
        md = sm.to_markdown(rep)
        self.assertIn("complete for this policy", md)

    def test_cli_writes_report(self):
        server = serve_fixture()
        try:
            with tempfile.TemporaryDirectory() as out:
                start = f"http://127.0.0.1:{server.server_address[1]}/about.html"
                code = sm.main([start, "--out", out, "--delay", "0", "--no-click", "--max-pages", "1"])
                self.assertEqual(code, 0)
                rep = json.loads((Path(out) / "map.json").read_text())
                self.assertIn(start, rep["pages"])
                self.assertTrue((Path(out) / "map.md").exists())
        finally:
            server.shutdown()
            server.server_close()

    def test_page_cap_counts_only_accepted_pages(self):
        m = sm.Mapper("http://127.0.0.1:9/p1", max_pages=2, log=lambda m: None, include=r"/p[0-9]$")
        m.enqueue("http://127.0.0.1:9/p1", "start")
        for i in range(5):
            m.enqueue(f"http://127.0.0.1:9/item{i}", "link")
        m.enqueue("http://127.0.0.1:9/p2", "link: next")
        m.enqueue("http://127.0.0.1:9/p3", "link: next")
        self.assertEqual(m.queue, ["http://127.0.0.1:9/p1", "http://127.0.0.1:9/p2"])
        self.assertEqual([e["page"] for e in m.ledger if e["reason"] == "page cap"], ["http://127.0.0.1:9/p3"])

    def test_extracts_js_rendered_products_and_scroll_feed(self):
        _Handler.writes = []
        server = serve_fixture()
        try:
            origin = f"http://127.0.0.1:{server.server_address[1]}"
            ex = extract.Extractor(extract.load("product"))
            sm.Mapper(origin + "/shop.html", delay=0.0, log=lambda m: None, extractor=ex,
                      include=r"/shop\.html", click=False).run()
            names = sorted(i["name"] for i in ex.items if i.get("name"))
            self.assertEqual(names, [f"Widget {c}" for c in "ABCDEF"])
            prices = {i["name"]: i["price"] for i in ex.items if i.get("name")}
            self.assertEqual(prices["Widget C"], 1250.99)
            feed = extract.Extractor({"name": "posts", "item": "li.post", "fields": {"text": {}}})
            sm.Mapper(origin + "/feed.html", delay=0.0, log=lambda m: None, extractor=feed,
                      include=r"/feed\.html", click=False, scroll=15).run()
            self.assertEqual(len({i["text"] for i in feed.items}), 15)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(_Handler.writes, [])


class Extraction(unittest.TestCase):
    def test_presets_load_and_validate(self):
        names = extract.presets()
        for want in ("text", "tables", "links", "prices", "product", "article"):
            self.assertIn(want, names)
            extract.load(want)
        self.assertNotIn("contacts", names)

    def test_schema_errors(self):
        bad = [[], {"fields": {}}, {"mode": "xml", "fields": {"a": {}}}, {"fields": {"a": {"css": "x", "eval": 1}}},
               {"fields": {"a": {"regex": "("}}}, {"fields": {"a": {"type": "date"}}}, {"oops": 1, "fields": {"a": {}}},
               {"match": "(", "fields": {"a": {}}}, {"mode": "jsonld", "jsonld_types": "Product"}]
        for schema in bad:
            with self.assertRaises(extract.SchemaError, msg=schema):
                extract.validate(schema)
        with self.assertRaises(extract.SchemaError):
            extract.load("no-such-preset-or-file")

    def test_convert_dedupe_and_match(self):
        ex = extract.Extractor({"name": "p", "match": "/shop", "fields": {
            "price": {"css": ".p", "regex": r"\$([0-9,.]+)", "type": "number"}, "qty": {"css": ".q", "type": "int"},
            "tags": {"css": ".t", "all": True}}})
        self.assertTrue(ex.wants("http://h/shop/1"))
        self.assertFalse(ex.wants("http://h/blog"))
        raw = [{"price": "now $1,250.50!", "qty": "3 left", "tags": ["a", "b"]},
               {"price": "now $1,250.50!", "qty": "3 left", "tags": ["a", "b"]},
               {"price": "free", "qty": None, "tags": []}]
        self.assertEqual(ex.add(raw, "http://h/shop/1", "s1"), 1)
        item = ex.items[0]
        self.assertEqual((item["price"], item["qty"], item["tags"]), (1250.5, 3, ["a", "b"]))
        self.assertEqual((item["_schema"], item["_url"], item["_state"]), ("p", "http://h/shop/1", "s1"))
        self.assertEqual(ex.summary()["field_fill"], {"price": 1, "qty": 1, "tags": 1})
        self.assertEqual(ex.add("not a list", "u", "s"), 0)

    def test_cli_check_schema(self):
        self.assertEqual(sm.main(["--check-schema", "product"]), 0)
        with tempfile.NamedTemporaryFile("w", suffix=".json") as fh:
            fh.write('{"fields": {"a": {"css": "x", "eval": "1"}}}')
            fh.flush()
            self.assertEqual(sm.main(["--check-schema", fh.name]), 2)

    def test_page_script_carries_only_data(self):
        ex = extract.Extractor(extract.load("product"))
        self.assertNotIn("%SCHEMA%", ex.js)
        self.assertIn('"mode": "css"', ex.js)


if __name__ == "__main__":
    unittest.main()
