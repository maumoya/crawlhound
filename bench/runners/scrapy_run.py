"""Run Scrapy (BSD-3) in a container: plain HTTP, no JavaScript, robots.txt obeyed, 1 request/s."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import UA, save, target  # noqa: E402

SPIDER = r'''
import json, re, scrapy
from scrapy.linkextractors import LinkExtractor
T = json.load(open("/work/target.json"))
def num(s):
    m = re.search(r"[0-9][0-9,]*\.?[0-9]*", s or "")
    return float(m.group(0).replace(",", "")) if m else None
class Bench(scrapy.Spider):
    name = "bench"
    start_urls = [T["start"]]
    custom_settings = {"ROBOTSTXT_OBEY": True, "DOWNLOAD_DELAY": 1, "CONCURRENT_REQUESTS": 1,
                       "USER_AGENT": T["ua"], "DEPTH_LIMIT": T.get("depth", 3), "LOG_LEVEL": "WARNING"}
    def parse(self, response):
        yield {"_page": response.url, "_forms": len(response.css("form"))}
        for el in response.css(T["item"]):
            item = {}
            for k, f in T["fields"].items():
                if f.get("attr"):
                    vals = el.css(f["css"] + "::attr(" + f["attr"] + ")").getall()
                else:
                    vals = [" ".join(" ".join(n.css("::text").getall()).split()) for n in el.css(f["css"])]
                vals = [num(v) for v in vals] if f.get("number") else vals
                item[k] = vals if f.get("all") else (vals[0] if vals else None)
            yield item
        for link in LinkExtractor(allow=T["scope"]).extract_links(response):
            yield response.follow(link.url, self.parse)
'''


def main() -> None:
    name = sys.argv[1]
    t = target(name, sys.argv[2] if len(sys.argv) > 2 else "")
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        work = Path(tmp)
        work.chmod(0o777)
        (work / "spider.py").write_text(SPIDER)
        (work / "target.json").write_text(json.dumps({**t, "ua": UA}))
        cmd = ["docker", "run", "--rm", "--add-host", "host.lima.internal:192.168.5.2", "-v", f"{work}:/work",
               "smbench-scrapy:1", "scrapy", "runspider", "/work/spider.py", "-o", "/work/out.jsonl"]
        began = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        seconds = round(time.time() - began, 1)
        out = work / "out.jsonl"
        rows = [json.loads(x) for x in out.read_text().splitlines() if x] if out.exists() else []
    pages = list({r["_page"]: r for r in rows if "_page" in r}.values())  # a page fetched twice counts once
    items = [r for r in rows if "_page" not in r]
    note = "scrapy 2.20.0, no JavaScript rendering, ROBOTSTXT_OBEY, 1 req/s"
    save("scrapy", name, {"items": items, "pages": len(pages), "forms": sum(r["_forms"] for r in pages),
                          "seconds": seconds, "status": "ok" if pages else "error",
                          "note": note if pages else note + "; " + proc.stderr[-300:]})


if __name__ == "__main__":
    main()
