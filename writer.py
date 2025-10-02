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

def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()

def ensure_dirs():
    OUT_DIR.mkdir(exist_ok=True, parents=True)
    DOCS_DIR.mkdir(exist_ok=True, parents=True)

def write_latest_json(payload: Dict[str, Any]) -> Path:
    ensure_dirs()
    p = OUT_DIR / "latest.json"
    with p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return p

def render_markdown(payload: Dict[str, Any], template_name: str = "report_md.j2") -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html", "xml"))
    )
    tmpl = env.get_template(template_name)
    return tmpl.render(**payload)

def write_latest_md(payload: Dict[str, Any]) -> Path:
    ensure_dirs()
    md = render_markdown(payload)
    p = OUT_DIR / "latest.md"
    p.write_text(md, encoding="utf-8")
    return p

def publish_to_docs():
    ensure_dirs()
    # copia latest.* a docs/
    for ext in ("md", "json"):
        src = OUT_DIR / f"latest.{ext}"
        if src.exists():
            (DOCS_DIR / f"latest.{ext}").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

def build_payload(universe: str, projects: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "generated_at_utc": utc_now_iso(),
        "universe": universe,
        "projects": projects
    }
