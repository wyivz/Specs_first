from __future__ import annotations

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install optional dependencies before running the UI: streamlit") from exc


GLOBAL_CSS = """
<style>
:root {
  --sf-accent: #2563eb;
  --sf-accent-soft: #dbeafe;
  --sf-ok: #059669;
  --sf-warn: #d97706;
  --sf-error: #dc2626;
  --sf-surface: #0f172a;
  --sf-border: #334155;
  --sf-text-primary: #f1f5f9;
  --sf-text-secondary: #e2e8f0;
  --sf-text-muted: #cbd5e1;
  --sf-text-subtle: #94a3b8;
  --sf-link: #60a5fa;
  --sf-link-hover: #93c5fd;
}
.sf-hero {
  background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%);
  border: 1px solid var(--sf-border);
  border-radius: 12px;
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}
.sf-hero h3 { margin: 0 0 0.35rem 0; color: #f8fafc; font-size: 1.05rem; }
.sf-hero p { margin: 0; color: #cbd5e1; font-size: 0.9rem; }
.sf-badge-row {
  display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.5rem 0 0.75rem 0;
}
.sf-pill {
  display: inline-block; padding: 0.2rem 0.65rem; border-radius: 999px;
  font-size: 0.78rem; font-weight: 600; border: 1px solid var(--sf-border);
  background: #1e293b; color: #e2e8f0;
}
.sf-pill-ok { border-color: #065f46; background: #064e3b; color: #a7f3d0; }
.sf-pill-warn { border-color: #92400e; background: #78350f; color: #fde68a; }
.sf-pill-error { border-color: #991b1b; background: #7f1d1d; color: #fecaca; }
.sf-pill-live { border-color: #1d4ed8; background: #1e3a8a; color: #bfdbfe; animation: sf-pulse 2s infinite; }
@keyframes sf-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.75; } }
.sf-table-wrap { overflow-x: auto; max-height: 70vh; border: 1px solid var(--sf-border); border-radius: 8px; }
.sf-table { width:100%; border-collapse:collapse; font-size:0.88rem; }
.sf-table th, .sf-table td { border:1px solid var(--sf-border); padding:8px 10px; vertical-align:top; }
.sf-table th { background:#1e293b; color:#f1f5f9; position:sticky; top:0; z-index:2; white-space:nowrap; }
.sf-table td.sf-sticky { position:sticky; left:0; background:var(--sf-surface); z-index:1; font-weight:600; color:var(--sf-text-primary); }
.sf-table th.sf-sticky { left:0; z-index:3; }
.sf-badge { margin-left:4px; font-size:0.8rem; }
.sf-conflict { background:#450a0a; color:#fecaca; }
.sf-warning { background:#422006; color:#fde68a; }
.sf-missing { color:var(--sf-text-subtle); font-style:italic; }
.sf-conflict .sf-missing { color:#fca5a5; }
.sf-warning .sf-missing { color:#fcd34d; }
.sf-evidence-link { font-size:0.75rem; display:block; margin-top:4px; color:var(--sf-link-hover); text-decoration:none; }
.sf-evidence-link:hover { color:var(--sf-link); text-decoration:underline; }
.sf-candidate {
  border:1px solid var(--sf-border); border-radius:8px; padding:0.65rem 0.85rem; margin-bottom:0.5rem;
  background:#111827; color:var(--sf-text-secondary);
}
.sf-candidate code { background:#1e293b; color:var(--sf-text-primary); padding:2px 6px; border-radius:4px; font-size:0.88em; }
.sf-candidate a { color:var(--sf-link); text-decoration:none; }
.sf-candidate a:hover { color:var(--sf-link-hover); text-decoration:underline; }
.sf-candidate .sf-muted { color:var(--sf-text-muted); font-size:0.82rem; }
.sf-step-active { font-weight:700; color:#60a5fa; }
</style>
"""


def inject_global_styles() -> None:
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
