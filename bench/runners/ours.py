"""Run crawlhound's spider.py on one benchmark target; usage: ours.py TARGET [FIXTURE_ORIGIN]."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import BENCH, save, target  # noqa: E402

SPIDER = BENCH.parent / "skills" / "crawlhound" / "scripts" / "spider.py"


def schema_for(t: dict) -> dict:
    fields = {}
    for name, f in t["fields"].items():
        spec = {"css": f["css"]}
        if "attr" in f:
            spec["attr"] = f["attr"]
        if f.get("all"):
            spec["all"] = True
        if f.get("number"):
            spec.update(regex=r"[0-9][0-9,]*\.?[0-9]*", type="number")
        fields[name] = spec
    return {"name": "bench", "item": t["item"], "fields": fields}


def main() -> None:
    name = sys.argv[1]
    origin = sys.argv[2] if len(sys.argv) > 2 else ""
    t = target(name, origin)
    with tempfile.TemporaryDirectory() as tmp:
        schema = Path(tmp) / "schema.json"
        schema.write_text(json.dumps(schema_for(t)))
        cmd = [sys.executable, str(SPIDER), t["start"], "--extract", str(schema), "--include", t["scope"],
               "--out", str(Path(tmp) / "out"), "--max-pages", "20"]
        if t.get("public"):
            cmd.append("--terms-ok")
        if t.get("scroll"):
            cmd += ["--scroll", "15"]
        began = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        seconds = round(time.time() - began, 1)
        out = Path(tmp) / "out"
        if proc.returncode != 0 or not (out / "map.json").exists():
            save("crawlhound", name, {"status": "error", "note": proc.stderr[-500:], "seconds": seconds})
            return
        rep = json.loads((out / "map.json").read_text())
        items = [json.loads(line) for line in (out / "items.jsonl").read_text().splitlines() if line]
        items = [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]
        cov = rep["coverage"]
        save("crawlhound", name, {
            "items": items, "pages": cov["pages"], "states": cov["states"], "forms": len(rep.get("forms", [])),
            "apis": len(rep.get("api", {})), "seconds": seconds,
            "blocked": len(rep.get("blocked_requests", [])), "note": "spider.py --extract, 1 req/s"})


if __name__ == "__main__":
    main()
