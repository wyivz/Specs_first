# Specs-First · Evidence-First Product Comparison

[中文](README.md) | **English**

> Skip the marketing fluff. Compare official specs, real-world flaws with evidence links, and actual checkout prices—in one table.

Specs-First is an **anti-hype, evidence-first** product comparison system. It gathers data from official sites, Bilibili/YouTube, geek forums, and e-commerce pages, runs a **Gemini + OpenAI** dual-brain pipeline, streams a comparison matrix in the UI, and persists results as **Obsidian** vault assets.

**No preset category templates.** After you pick SKUs, Gemini surveys detail images for parameter clues, then ChatGPT Structured Outputs locks a per-task JIT schema: category label, 5–8 hard slots, aliases, and search keywords—works for any product type.

---

## Problem Statement

| Pain point | Specs-First approach |
|------------|---------------------|
| Inflated marketing specs | Lock datasheet values as the `official` baseline |
| Sponsored reviews hiding flaws | Gemini keeps only evidence-backed issues |
| Unclear checkout prices | Parse subsidies/coupons; flag conflicts with links |
| Inconsistent comparison axes | **JIT 5–8 hard slots** (vision survey → schema) + `spec_highlights` |

---

## Pipeline

```
Phase 0 Discover SKUs
    │  after selection ──► 0.5 JIT schema (Gemini vision → ChatGPT lock slots)
    ▼
Phase 1 Official specs ──► Phase 2 Field reports ──► Phase 3 Price/OCR ──► Phase 4 Arbitration & Obsidian
```

| Phase | What | Model role |
|-------|------|------------|
| 0 | Up to 10 candidate SKUs; user selects | — |
| 0.5 | Probe detail images → category + slots + aliases + search modifiers | Gemini survey + OpenAI schema |
| 1 | Fill official slots from manufacturer / e-commerce pages | Gemini text & image fill |
| 2 | Dehydrate Bilibili / YouTube / forum evidence | Gemini QA pass |
| 3 | Checkout price + screenshot OCR | Gemini OCR |
| 4 | Conflict arbitration + Obsidian/CSV export | OpenAI Structured Outputs |

Without API keys, a **keyword rules engine** runs (slots fall back to `parameter_a…h`). Mock mode needs no network.

---

## Architecture

| Layer | Stack | Role |
|-------|-------|------|
| Frontend | Streamlit | SKU picker, streaming matrix, JIT schema caption, captcha browser |
| Backend | FastAPI + threads | Tasks, events, checkpoint resume |
| Collectors | HTTP + Playwright + AdapterRegistry | Official / video / forum / JD / Taobao-Tmall |
| Models | Gemini + OpenAI | Vision/detox/OCR + JIT schema & arbitration |
| Output | Obsidian + Dataview + CSV | Durable local assets |

```
Streamlit UI
    └── frontend/api_client.py
            └── backend/task_runner.py
                    └── pipeline.py (JIT schema bootstrap)
                            ├── candidate_processor.py
                            ├── collectors/real.py → sources/ + adapters/
                            ├── model_router (keyword | hybrid)
                            └── obsidian/writer.py
```

- Config lives in `collectors/settings.py`; `backend/config.py` re-exports
- `RealCollector` receives injected `router` + `DynamicCategoryProfile`
- Collectors do not import `backend`

---

## Status (2026-07)

### Done

- [x] End-to-end Mock / Real pipeline with per-SKU isolation
- [x] Four-phase pipeline + **universal JIT category schema** (no preset lens/phone templates)
- [x] Dual-brain routers (`router_keyword` / `router_hybrid` / `router_schemas`)
- [x] FastAPI + Streamlit (`api_client`) + progressive matrix events
- [x] Captcha HITL: `PAUSED_NEED_AUTH`, embedded browser, resume
- [x] Adapters: JD, Bilibili, YouTube, Taobao/Tmall (mtop + Cookie)
- [x] AdapterRegistry wiring; collector dependency inversion
- [x] Obsidian + Dataview + CSV
- [x] Health checks: `GET /health`, `scripts/smoke_platforms.py`
- [x] **138 unit tests** + GitHub Actions CI

### Needs live setup

- [~] Gemini / OpenAI keys in `.env` for Real-mode JIT schema and arbitration
- [~] Taobao / JD cookies expire and must be refreshed from the browser

### Frozen

- ⏸️ Historical price curves, Notion sync

---

## Quick Start

### Requirements

- Python 3.12+
- Real mode: `playwright install chromium`

### Install

```powershell
cd Specs-first
pip install fastapi uvicorn streamlit httpx openai google-generativeai redis playwright
pip install -e .
```

### Mock demo (no API keys)

```powershell
python -m backend.pipeline
```

Writes to `vault_output/` by default.

### Web UI (recommended)

```powershell
streamlit run frontend/app.py
```

Choose `mock` or `real` in the sidebar; enable Playwright for Real.

### API (optional)

```powershell
uvicorn backend.api:app --reload
```

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Config / credential health |
| `POST /discover` | Discover candidate SKUs |
| `POST /tasks` | Start compare (includes JIT schema) |
| `GET /tasks/{id}/events` | SSE stream (includes `category_profile_ready`) |
| `GET /tasks/{id}/result` | Matrix + vault paths |
| `POST /tasks/{id}/resume-auth` | Resume after captcha |
| `GET /tasks/{id}/diagnostics` | Collector diagnostics |
| `GET /asr/status` · `POST /asr/transcribe` | Local ASR |

### Tests

```powershell
python -m unittest discover -s tests
```

**138** unit tests passing (excluding live smoke).

```powershell
python scripts/smoke_platforms.py --health-only
```

---

## Real-mode configuration

Copy `.env.example` → `.env`:

```env
GEMINI_API_KEY=...
OPENAI_API_KEY=...
DEFAULT_GEMINI_MODEL=gemini-2.5-flash
DEFAULT_OPENAI_MODEL=gpt-4o-mini
OBSIDIAN_VAULT_PATH=./vault_output
```

| Platform | Variables | Notes |
|----------|-----------|-------|
| Bilibili | `BILIBILI_SESSDATA`, … | Subtitles + top comments |
| Taobao/Tmall | `TAOBAO_COOKIE` | Prefer cookies with `_m_h5_tk` |
| JD | `JD_COOKIE` | Mainland network recommended |
| Reddit (optional) | `REDDIT_COOKIE` | Skipped if unset |

**Suggested flow:** Streamlit `real` → Playwright on → leave category hint empty → Start compare → solve captcha in-page if needed. Source URLs are optional; direct product links are more reliable.

### Real-run checklist

Pass criteria: **non-empty JIT slots + at least one of price/specs/evidence** (not “every platform full”).

1. Configure P0 secrets in `.env`: Gemini + OpenAI, `SPECS_FIRST_MODE=real`, `JD_COOKIE`, Taobao cookie/`_m_h5_tk`, Bilibili cookie trio.
2. `python scripts/smoke_platforms.py --probe-gemini`
3. Paste into Source URLs (or `OPTIONAL_SOURCE_URLS`): one JD item, one Taobao/Tmall item, 1–2 real review videos.
4. Streamlit `real` + Playwright; use a concrete model number.
5. Ecommerce pacing defaults to ~3s + jitter; JD frequency-control triggers host backoff (no headed captcha spam).

---

## Repository layout

```
Specs-first/
├── backend/           # pipeline (JIT bootstrap), API, routers, task_runner
├── collectors/        # settings, real/mock, sources/, adapters/
├── frontend/          # Streamlit + api_client
├── schemas/           # models + DynamicCategoryProfile + matrix
├── obsidian/          # vault writer + CSV
├── scripts/           # smoke_platforms.py
├── tests/             # 138 tests
├── plan.md            # architecture plan
└── .github/workflows/ # CI
```

---

## Obsidian output

```
vault_output/
├── 00_Specs_First_Matrix/
│   ├── *_comparison_matrix.md
│   └── *.csv
└── 01_Product_Items/
    └── <sku>.md
```

Enable the **Dataview** plugin to render the matrix locally.

---

## Next steps

| Priority | Item |
|----------|------|
| P0 | Real run on a machine: `.env` + Playwright + cookies |
| P1 | Tune JIT schema / arbitration prompts |
| P2 | Bilibili WBI, YouTube captions, richer Reddit evidence |
| Cloud later | Split API, CORS/rate limits, Redis, Docker |

---

## License

**GNU GPL v3.0** — see [LICENSE](LICENSE)

## Related docs

- [中文 README](README.md)
- [Architecture plan](plan.md)
