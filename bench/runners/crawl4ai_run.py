"""Run Crawl4AI (Apache-2.0) in a container: headless Chromium with JsonCssExtractionStrategy, full-page scan
for scroll targets. Crawl4AI fetches one URL per call, so this runner follows in-scope links breadth-first itself
(the target's depth, 1 request per second) and checks robots.txt with check_robots_txt. Crawl4AI files host:port links as
"external", so both link buckets are read and the target scope regex decides. Links are taken as Crawl4AI resolves
them: 0.6.3 resolves relative hrefs against a .html page as if it were a folder (page-1.html/page-2.html), which
ends the books crawl after page 1. That is recorded as Crawl4AI's result, not patched here."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import UA, save, target  # noqa: E402

SCRIPT = r'''
import asyncio, json, re, time
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
T = json.load(open("/work/target.json"))
fields = []
for k, f in T["fields"].items():
    if f.get("all"):
        fields.append({"name": k, "selector": f["css"], "type": "list", "fields": [{"name": "v", "type": "text"}]})
    elif f.get("attr"):
        fields.append({"name": k, "selector": f["css"], "type": "attribute", "attribute": f["attr"]})
    else:
        fields.append({"name": k, "selector": f["css"], "type": "text"})
schema = {"name": "bench", "baseSelector": T["item"], "fields": fields}
scope = re.compile(T["scope"])
async def main():
    out = {"items": [], "pages": [], "forms": 0}
    run = CrawlerRunConfig(extraction_strategy=JsonCssExtractionStrategy(schema), cache_mode=CacheMode.BYPASS,
                           check_robots_txt=True, scan_full_page=bool(T.get("scroll")), wait_until="networkidle",
                           delay_before_return_html=1.0)
    async with AsyncWebCrawler(config=BrowserConfig(headless=True, user_agent=T["ua"])) as crawler:
        seen, queue = {T["start"]}, [(T["start"], 0)]
        while queue and len(out["pages"]) < 50:
            url, depth = queue.pop(0)
            r = await crawler.arun(url, config=run)
            time.sleep(1)
            if not r.success:
                continue
            out["pages"].append(url)
            out["forms"] += len(re.findall(r"<form\b", r.html or "", re.I))
            for it in json.loads(r.extracted_content or "[]"):
                for k, f in T["fields"].items():
                    if f.get("all") and isinstance(it.get(k), list):
                        it[k] = [x.get("v") for x in it[k] if isinstance(x, dict)]
                out["items"].append(it)
            if depth < T.get("depth", 3):
                for link in (r.links or {}).get("internal", []) + (r.links or {}).get("external", []):
                    href = link.get("href", "").split("#")[0]
                    if href and scope.search(href) and href not in seen:
                        seen.add(href); queue.append((href, depth + 1))
    json.dump(out, open("/work/out.json", "w"))
asyncio.run(main())
'''


def num(v):
    import re
    m = re.search(r"[0-9][0-9,]*\.?[0-9]*", v or "") if isinstance(v, str) else None
    return float(m.group(0).replace(",", "")) if m else None


def main() -> None:
    name = sys.argv[1]
    t = target(name, sys.argv[2] if len(sys.argv) > 2 else "")
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        work = Path(tmp)
        work.chmod(0o777)
        (work / "run.py").write_text(SCRIPT)
        (work / "target.json").write_text(json.dumps({**t, "ua": UA}))
        cmd = ["docker", "run", "--rm", "--ipc=host", "--add-host", "host.lima.internal:192.168.5.2",
               "-v", f"{work}:/work", "-e", "HOME=/tmp", "smbench-crawl4ai:1", "python", "/work/run.py"]
        began = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        seconds = round(time.time() - began, 1)
        out_file = work / "out.json"
        out = json.loads(out_file.read_text()) if out_file.exists() else None
    note = "crawl4ai 0.9.5, JsonCssExtractionStrategy, check_robots_txt, runner-driven BFS at 1 req/s"
    if not out:
        save("crawl4ai", name, {"status": "error", "seconds": seconds, "items": [], "note": note + "; " + proc.stderr[-400:]})
        return
    for item in out["items"]:
        for k, f in t["fields"].items():
            if f.get("number"):
                item[k] = num(item.get(k))
    save("crawl4ai", name, {"items": out["items"], "pages": len(set(out["pages"])), "forms": out["forms"],
                            "seconds": seconds, "note": note})


if __name__ == "__main__":
    main()
