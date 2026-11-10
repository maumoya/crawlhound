"""Run Crawlee (Apache-2.0) PlaywrightCrawler in a container: headless Chromium, robots.txt respected,
one request at a time, at most 60 per minute, infinite scroll on scroll targets."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import UA, save, target  # noqa: E402

SCRIPT = r'''
const fs = require('fs');
const { PlaywrightCrawler, Configuration } = require('/opt/bench/node_modules/crawlee');
const T = JSON.parse(fs.readFileSync('/work/target.json', 'utf8'));
const out = { items: [], pages: [], forms: 0, apis: new Set() };
const crawler = new PlaywrightCrawler({
  maxConcurrency: 1, maxRequestsPerMinute: 60, maxRequestsPerCrawl: 50, respectRobotsTxtFile: true,
  launchContext: { userAgent: T.ua },
  preNavigationHooks: [async ({ page }) => {
    page.on('request', (r) => { if (['xhr', 'fetch'].includes(r.resourceType()) && r.method() === 'GET') out.apis.add(r.url()); });
  }],
  async requestHandler({ page, request, enqueueLinks, infiniteScroll }) {
    await page.waitForLoadState('networkidle').catch(() => {});
    if (T.scroll) await infiniteScroll({ timeoutSecs: 30 });
    out.pages.push(request.loadedUrl || request.url);
    out.forms += await page.$$eval('form', (f) => f.length);
    const items = await page.$$eval(T.item, (els, fields) => els.map((el) => {
      const o = {};
      for (const [k, f] of Object.entries(fields)) {
        const nodes = [...el.querySelectorAll(f.css)];
        const vals = nodes.map((n) => f.attr ? n.getAttribute(f.attr) : n.innerText.replace(/\s+/g, ' ').trim());
        o[k] = f.all ? vals : (vals[0] ?? null);
      }
      return o;
    }), T.fields);
    out.items.push(...items);
    await enqueueLinks({ regexps: [new RegExp(T.scope)] });
  },
}, new Configuration({ persistStorage: false, purgeOnStart: true }));
crawler.run([T.start]).then(() => {
  fs.writeFileSync('/work/out.json', JSON.stringify({ ...out, apis: [...out.apis] }));
});
'''


def num(v):
    import re
    m = re.search(r"[0-9][0-9,]*\.?[0-9]*", v or "")
    return float(m.group(0).replace(",", "")) if m else None


def main() -> None:
    name = sys.argv[1]
    t = target(name, sys.argv[2] if len(sys.argv) > 2 else "")
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        work = Path(tmp)
        work.chmod(0o777)
        (work / "run.js").write_text(SCRIPT)
        (work / "target.json").write_text(json.dumps({**t, "ua": UA}))
        cmd = ["docker", "run", "--rm", "--ipc=host", "--add-host", "host.lima.internal:192.168.5.2",
               "-v", f"{work}:/work", "-e", "CRAWLEE_STORAGE_DIR=/tmp/crawlee", "smbench-crawlee:1",
               "node", "/work/run.js"]
        began = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        seconds = round(time.time() - began, 1)
        out_file = work / "out.json"
        out = json.loads(out_file.read_text()) if out_file.exists() else None
    note = "crawlee 3.18.2 PlaywrightCrawler, respectRobotsTxtFile, concurrency 1, 60 req/min"
    if not out:
        save("crawlee", name, {"status": "error", "seconds": seconds, "items": [], "note": note + "; " + proc.stderr[-400:]})
        return
    for item in out["items"]:
        for k, f in t["fields"].items():
            if f.get("number"):
                item[k] = num(item.get(k))
    save("crawlee", name, {"items": out["items"], "pages": len(set(out["pages"])), "forms": out["forms"],
                           "apis": len(out["apis"]), "seconds": seconds, "note": note})


if __name__ == "__main__":
    main()
