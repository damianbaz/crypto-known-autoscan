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

# ---------- Discovery block helper ----------
def _md_discovery_block(discovery: Dict[str, Any] | None) -> str:
    d = discovery or {}
    samp = d.get("discovery_sample") or []
    quick = d.get("quick_suggestions") or []

    # If you only want to show the section when there’s content, uncomment:
    # if not samp and not quick:
    #     return ""

    lines: List[str] = []
    lines.append("\n---\n")
    lines.append("## Discovery & Quick Suggestions\n")

    # Sample
    lines.append(f"**Muestras (top por score, máx 10): {len(samp)}**")
    for i, it in enumerate(samp, 1):
        sym = it.get("symbol", "?")
        sc  = it.get("score", 0)
        vol = it.get("vol", 0)
        try:
            vol_str = f"{vol:,.0f}"
        except Exception:
            vol_str = str(vol)
        lines.append(f"{i}. **{sym}** — score {sc}, vol24h ${vol_str}")

    lines.append("")
    # Quick suggestions
    lines.append(f"**Quick suggestions (máx 10): {len(quick)}**")
    for i, q in enumerate(quick, 1):
        act = q.get("action", "?")
        sym = q.get("symbol", "?")
        rsn = q.get("reason", "")
        tp  = int((q.get("tp_pct") or 0) * 100)
        sl  = int((q.get("sl_pct") or 0) * 100)
        lines.append(f"{i}. {act} **{sym}** — {rsn} (TP {tp}%, SL {sl}%)")

    return "\n".join(lines) + "\n"

def write_latest_json(payload: Dict[str, Any]) -> Path:
    ensure_dirs()
    p = OUT_DIR / "latest.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p

def write_latest_md(payload: Dict[str, Any]) -> Path:
    """
    Renders the main report via Jinja, then appends the Discovery section.
    """
    ensure_dirs()
    md = render_markdown(payload)  # whatever your template outputs
    md += _md_discovery_block(payload.get("discovery") or {})  # <-- append here
    p = OUT_DIR / "latest.md"
    p.write_text(md, encoding="utf-8")
    return p

def write_dated(payload: Dict[str, Any]) -> None:
    """
    Writes report-YYYY-MM-DD.{json,md} into out/. The MD also appends Discovery.
    """
    ensure_dirs()
    d = today_str()

    # JSON
    (OUT_DIR / f"report-{d}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # MD
    md = render_markdown(payload)
    md += _md_discovery_block(payload.get("discovery") or {})  # <-- append here
    (OUT_DIR / f"report-{d}.md").write_text(md, encoding="utf-8")

def publish_to_docs():
    """
    Copies latest.* and report-YYYY-MM-DD.* from out/ to docs/.
    """
    ensure_dirs()
    for ext in ("md", "json"):
        # latest
        src = OUT_DIR / f"latest.{ext}"
        if src.exists():
            (DOCS_DIR / f"latest.{ext}").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        # dated (today)
        from_date = today_str()
        dated = OUT_DIR / f"report-{from_date}.{ext}"
        if dated.exists():
            (DOCS_DIR / dated.name).write_text(dated.read_text(encoding="utf-8"), encoding="utf-8")

def build_payload(universe: str, projects: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "generated_at_utc": utc_now_iso(),
        "universe": universe,
        "projects": projects
        # NOTE: main() should set payload["discovery"] before calling writer.
    }
