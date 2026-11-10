"""Data extraction for the spider: a small JSON schema (or a preset name) says what to pull from each page state.

Schema shape (every key but "fields" is optional):

    {
      "name": "quotes",                 # label written into every item
      "match": "/js/",                  # regex on the state URL; states that do not match are not extracted
      "mode": "css",                    # "css" (default), "tables" or "jsonld"
      "item": ".quote",                 # CSS selector: one item per match ("css" mode; default "html" = one per page)
      "fields": {
        "text":   {"css": ".text"},                    # innerText of the first match inside the item
        "body":   {"css": "main || article || body"},  # "||" = fallbacks: the first selector that matches wins
        "tags":   {"css": ".tag", "all": true},        # list of every match
        "link":   {"css": "a", "attr": "href"},        # an attribute (href/src resolved to absolute URLs)
        "price":  {"css": ".price", "regex": "[0-9.]+", "type": "number"},
        "self":   {}                                   # no css = the item element itself
      },
      "jsonld_types": ["Product"]       # "jsonld" mode: keep JSON-LD objects of these @type values
    }

The page script only reads the DOM and returns strings; regex and type conversion run here in Python, so a schema
can never make the page do anything. Values are capped in length and items per state are capped.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PRESET_DIR = Path(__file__).resolve().parent.parent / "presets"
MODES = ("css", "tables", "jsonld")
FIELD_KEYS = {"css", "attr", "all", "regex", "type"}
SCHEMA_KEYS = {"name", "match", "mode", "item", "fields", "jsonld_types", "description"}
TYPES = ("text", "number", "int")
VALUE_MAX = 4000
ITEMS_MAX = 2000
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class SchemaError(ValueError):
    """The schema file or preset is not valid."""


def presets() -> list:
    return sorted(p.stem for p in PRESET_DIR.glob("*.json"))


def load(spec: str) -> dict:
    """A preset name or a path to a JSON schema file, validated."""
    if NAME_RE.match(spec or "") and (PRESET_DIR / f"{spec}.json").is_file():
        path = PRESET_DIR / f"{spec}.json"
    else:
        path = Path(spec)
        if not path.is_file():
            raise SchemaError(f"no preset or schema file named {spec!r} (presets: {', '.join(presets())})")
    try:
        schema = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaError(f"{path}: not valid JSON ({exc})") from exc
    return validate(schema)


def validate(schema: dict) -> dict:
    if not isinstance(schema, dict):
        raise SchemaError("schema must be a JSON object")
    unknown = set(schema) - SCHEMA_KEYS
    if unknown:
        raise SchemaError(f"unknown schema keys: {', '.join(sorted(unknown))}")
    mode = schema.get("mode", "css")
    if mode not in MODES:
        raise SchemaError(f"mode must be one of {', '.join(MODES)}")
    for key in ("name", "match", "item"):
        if key in schema and not isinstance(schema[key], str):
            raise SchemaError(f"{key} must be a string")
    if "match" in schema:
        _compile(schema["match"], "match")
    fields = schema.get("fields", {})
    if mode == "css" and not fields:
        raise SchemaError("css mode needs at least one field")
    if not isinstance(fields, dict):
        raise SchemaError("fields must be an object")
    for name, spec in fields.items():
        if not isinstance(spec, dict) or set(spec) - FIELD_KEYS:
            raise SchemaError(f"field {name!r}: allowed keys are {', '.join(sorted(FIELD_KEYS))}")
        if "regex" in spec:
            _compile(spec["regex"], f"field {name!r} regex")
        if spec.get("type", "text") not in TYPES:
            raise SchemaError(f"field {name!r}: type must be one of {', '.join(TYPES)}")
    types = schema.get("jsonld_types", [])
    if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
        raise SchemaError("jsonld_types must be a list of strings")
    return {**schema, "mode": mode, "name": schema.get("name", "items"), "fields": fields}


def _compile(pattern: str, where: str):
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise SchemaError(f"{where}: bad regex ({exc})") from exc


EXTRACT_JS = r"""
((S) => {
  const MAX = %VALUEMAX%, ITEMS = %ITEMSMAX%;
  const clip = (s) => (s == null ? null : String(s).replace(/\s+/g, ' ').trim().slice(0, MAX));
  const URLATTR = /^(href|src|action|poster|data-src)$/i;
  const read = (el, attr) => {
    if (!el) return null;
    if (!attr || attr === 'text') return clip(el.innerText !== undefined ? el.innerText : el.textContent);
    for (const a of attr.split('|')) {
      const v = el.getAttribute(a);
      if (v != null) return clip(URLATTR.test(a) ? new URL(v, location.href).href : v);
    }
    return null;
  };
  const query = (root, css) => {
    for (const sel of css.split('||')) {
      let els = [];
      try { els = [...root.querySelectorAll(sel.trim())]; } catch (e) {}
      if (els.length) return els;
    }
    return [];
  };
  const pick = (root, f) => {
    if (!f.css) return f.all ? [read(root, f.attr)] : read(root, f.attr);
    const els = query(root, f.css);
    return f.all ? els.map((e) => read(e, f.attr)).filter((v) => v != null) : read(els[0], f.attr);
  };
  if (S.mode === 'tables') {
    return [...document.querySelectorAll('table')].slice(0, ITEMS).map((t, i) => {
      const rows = [...t.rows].filter((r) => r.parentElement !== t.tHead).map((r) => [...r.cells].map((c) => clip(c.innerText)));
      const head = t.tHead && t.tHead.rows[0] ? [...t.tHead.rows[0].cells].map((c) => clip(c.innerText)) : null;
      return {table: i, caption: t.caption ? clip(t.caption.innerText) : null, header: head, rows: rows.slice(0, 1000)};
    });
  }
  if (S.mode === 'jsonld') {
    const out = [];
    const want = new Set(S.jsonld_types || []);
    const walk = (o) => {
      if (Array.isArray(o)) return o.forEach(walk);
      if (!o || typeof o !== 'object') return;
      if (o['@graph']) walk(o['@graph']);
      const t = [].concat(o['@type'] || []);
      if (!want.size || t.some((x) => want.has(x))) out.push(o);
    };
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try { walk(JSON.parse(s.textContent)); } catch (e) {}
    }
    const fields = {};
    for (const [k, f] of Object.entries(S.fields || {})) fields[k] = pick(document, f);
    if (!out.length && Object.values(fields).some((v) => v != null && (!Array.isArray(v) || v.length))) out.push({});
    return out.slice(0, ITEMS).map((o) => Object.assign({}, Object.keys(fields).length ? fields : {}, {jsonld: JSON.parse(JSON.stringify(o).slice(0, MAX * 4)) }));
  }
  const roots = query(document, S.item || 'html').slice(0, ITEMS);
  return roots.map((r) => { const o = {}; for (const [k, f] of Object.entries(S.fields)) o[k] = pick(r, f); return o; });
})(%SCHEMA%)
"""


class Extractor:
    def __init__(self, schema: dict):
        self.schema = validate(schema)
        self.match = re.compile(self.schema["match"]) if self.schema.get("match") else None
        self.regex = {k: re.compile(f["regex"]) for k, f in self.schema["fields"].items() if "regex" in f}
        page_side = {k: self.schema.get(k) for k in ("mode", "item", "fields", "jsonld_types")}
        self.js = (EXTRACT_JS.replace("%VALUEMAX%", str(VALUE_MAX)).replace("%ITEMSMAX%", str(ITEMS_MAX))
                   .replace("%SCHEMA%", json.dumps(page_side)))
        self.items, self.seen = [], set()
        self.states_matched = 0

    def wants(self, url: str) -> bool:
        return not self.match or bool(self.match.search(url))

    def _convert(self, name: str, value):
        if isinstance(value, list):
            return [v for v in (self._convert(name, x) for x in value) if v is not None]
        if value is None or not isinstance(value, str):
            return value
        rx = self.regex.get(name)
        if rx:
            m = rx.search(value)
            if not m:
                return None
            value = m.group(1) if m.groups() else m.group(0)
        kind = self.schema["fields"].get(name, {}).get("type", "text")
        if kind in ("number", "int"):
            digits = re.sub(r"[^0-9.\-]", "", value.replace(",", ""))
            try:
                return int(float(digits)) if kind == "int" else float(digits)
            except ValueError:
                return None
        return value

    def add(self, raw, url: str, state: str) -> int:
        """Convert, de-duplicate and keep items from one state; returns how many were new."""
        if not isinstance(raw, list):
            return 0
        self.states_matched += 1
        new = 0
        for obj in raw[:ITEMS_MAX]:
            if not isinstance(obj, dict):
                continue
            item = {k: self._convert(k, v) for k, v in obj.items()}
            if self.schema["mode"] == "css" and all(v in (None, [], "") for v in item.values()):
                continue
            key = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
            if key in self.seen:
                continue
            self.seen.add(key)
            self.items.append({"_schema": self.schema["name"], "_url": url, "_state": state, **item})
            new += 1
        return new

    def summary(self) -> dict:
        fields = list(self.schema["fields"])
        filled = {f: sum(1 for i in self.items if i.get(f) not in (None, [], "")) for f in fields}
        return {"schema": self.schema["name"], "mode": self.schema["mode"], "items": len(self.items),
                "states_matched": self.states_matched, "field_fill": filled}
