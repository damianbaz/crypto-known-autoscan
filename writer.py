from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "out"
DOCS_DIR = ROOT / "docs"
TEMPLATES_DIR = ROOT / "templates"

def utc_now():
    return datetime.now(tz=timezone.utc).replace(microsecond=0)

def utc_now_iso() -> str:
    return utc_now().isoformat()

def today_str() -> str:
    return utc_now().date().isoformat()  # YYYY-MM-DD

def ensure_dirs():
    OUT_DIR.mkdir(exist_ok=True, parents=True)
    DOCS_DIR.mkdir(exist_ok=True, parents=True)

def render_markdown(payload: Dict[str, Any], template_name: str = "report_md.j2") -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html", "xml"))
    )
    return env.get_template(template_name).render(**payload)

def write_latest_json(payload: Dict[str, Any]) -> Path:
    ensure_dirs()
    p = OUT_DIR / "latest.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p

def write_latest_md(payload: Dict[str, Any]) -> Path:
    ensure_dirs()
    md = render_markdown(payload)
    p = OUT_DIR / "latest.md"
    p.write_text(md, encoding="utf-8")
    return p

def write_dated(payload: Dict[str, Any]) -> None:
    """Escribe también report-YYYY-MM-DD.{md,json} en out/"""
    ensure_dirs()
    d = today_str()
    (OUT_DIR / f"report-{d}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = render_markdown(payload)
    (OUT_DIR / f"report-{d}.md").write_text(md, encoding="utf-8")

def publish_to_docs():
    """Copia latest.* y report-YYYY-MM-DD.* a docs/"""
    ensure_dirs()
    for ext in ("md", "json"):
        # latest
        src = OUT_DIR / f"latest.{ext}"
        if src.exists():
            (DOCS_DIR / f"latest.{ext}").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        # dated (del día)
        from_date = today_str()
        dated = OUT_DIR / f"report-{from_date}.{ext}"
        if dated.exists():
            (DOCS_DIR / dated.name).write_text(dated.read_text(encoding="utf-8"), encoding="utf-8")

def build_payload(universe: str, projects: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "generated_at_utc": utc_now_iso(),
        "universe": universe,
        "projects": projects
    }
