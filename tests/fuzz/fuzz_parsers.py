"""Fuzz the parsers that read untrusted input: robots.txt, WebSocket frames from the browser, URLs and schemas.

Run with Atheris:   python3 tests/fuzz/fuzz_parsers.py -max_total_time=60
Without Atheris it replays the seed corpus once (what the unit tests do), so it never needs a network install.
A crash is any exception other than the documented ones (SchemaError, ValueError from a bad URL)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "crawlhound" / "scripts"))
import cdp  # noqa: E402
import extract  # noqa: E402
import spider  # noqa: E402

SEEDS = [
    b"User-agent: *\nDisallow: /private\nAllow: /private/ok$\nCrawl-delay: 2\nSitemap: /sitemap.xml\n",
    b"\x81\x05hello",
    b"\x81\x7e\x00\x03abc",
    b'{"name": "x", "fields": [{"name": "title", "selector": "h1"}]}',
    b"http://127.0.0.1:8000/a/../b?q=1#frag",
]


def one_input(data: bytes) -> None:
    text = data.decode("utf-8", "replace")
    robots = spider.Robots(text)
    robots.allowed("http://127.0.0.1/" + text[:64])
    frames, rest = cdp.decode_frames(data)
    assert len(rest) <= len(data)
    for _fin, _op, payload in frames:
        assert len(payload) <= len(data)
    try:
        spider.normalize(text, "http://127.0.0.1/")
    except ValueError:
        pass
    try:
        extract.validate(json.loads(text))
    except ValueError:  # bad JSON, or SchemaError (a ValueError)
        pass


def main() -> int:
    try:
        import atheris
    except ImportError:
        for seed in SEEDS:
            one_input(seed)
        print(f"replayed {len(SEEDS)} seeds (install atheris to fuzz)")
        return 0
    atheris.instrument_all()
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":
    sys.exit(main())
