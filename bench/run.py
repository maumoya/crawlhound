"""Run one tool on one target: starts the logging fixture server for the fixture target, then the runner.

usage: run.py TOOL TARGET     (TOOL = crawlhound | scrapy | katana | crawlee | crawl4ai)
Docker tools reach the host fixture through host.lima.internal (colima) / host.docker.internal."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture_server  # noqa: E402
from common import BENCH, RESULTS  # noqa: E402

DOCKER_HOST_NAME = os.environ.get("BENCH_HOST_NAME", "host.lima.internal")
RUNNERS = {"crawlhound": "ours.py", "scrapy": "scrapy_run.py", "katana": "katana_run.py",
           "crawlee": "crawlee_run.py", "crawl4ai": "crawl4ai_run.py"}


def main() -> None:
    tool, name = sys.argv[1], sys.argv[2]
    origin, log, server = "", None, None
    if name == "fixture":
        log = RESULTS / tool / "fixture-server.jsonl"
        bind = "127.0.0.1"
        public = "127.0.0.1" if tool == "crawlhound" else DOCKER_HOST_NAME
        server, origin = fixture_server.start(log, host=bind, public_host=public)
    try:
        subprocess.run([sys.executable, str(BENCH / "runners" / RUNNERS[tool]), name, origin], check=False)
    finally:
        if server:
            server.shutdown()
            server.server_close()
    if log:
        hits = [json.loads(line) for line in log.read_text().splitlines() if line]
        res_path = RESULTS / tool / f"{name}.json"
        res = json.loads(res_path.read_text())
        res["server"] = {"requests": len(hits),
                         "writes": sum(1 for h in hits if h["method"] not in ("GET", "HEAD")),
                         "robots_violations": sum(1 for h in hits if h["path"].startswith("/private/")),
                         "write_paths": sorted({h["method"] + " " + h["path"] for h in hits if h["method"] not in ("GET", "HEAD")})}
        res_path.write_text(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
