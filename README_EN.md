# Specs-First · Evidence-First Product Comparison

[中文](README.md) | **English**

> Skip the marketing fluff. Compare official specs, real-world flaws with evidence links, and actual checkout prices—in one table.

Specs-First is an **anti-hype, evidence-first** product comparison system. It automatically gathers data from official sites, Bilibili/YouTube, geek forums, and e-commerce pages, processes it through a **Gemini dehydration + OpenAI arbitration** dual-brain pipeline, streams a comparison matrix to the UI, and persists results as **Obsidian** knowledge-base assets.

---

## Problem Statement

| Pain point | Specs-First approach |
|------------|---------------------|
| Inflated marketing specs | Lock official datasheet values as the `official` baseline |
| Sponsored reviews hiding flaws | Gemini acts as a harsh QA reviewer—praise filtered, flaws kept with sources |
| Unclear real checkout prices | Parse subsidies/coupons from product pages; conflicts flagged with evidence |
| Inconsistent comparison axes | Fixed 5–8 category columns + `spec_highlights` attribute bucket |

---

## Core Logic (Four-Phase Pipeline)

```
Phase 0 Disambiguation ──► Phase 1 Official Base ──► Phase 2 Dehydration ──► Phase 3 Price/Vision ──► Phase 4 Arbitration & Export
  fuzzy query → pick SKUs     website/whitepaper specs    Gemini detox          e-commerce prices          OpenAI verdict + Obsidian
```

### Phase 0 · Intent disambiguation

Input such as `Zeiss 50mm lens` yields up to 10 candidate SKUs for user selection before comparison.

### Phase 1 · Official skeleton

Targeted retrieval from brand sites, manuals, and whitepapers for focal length, aperture, weight, optical structure, and other **immutable official specs**.

### Phase 2 · Real-world dehydration

Concurrent fetch from Bilibili comments/danmaku, YouTube, Chiphell, Reddit, etc. **Gemini Flash** strips phrases like “legendary bokeh” and keeps only evidence-backed flaws.

### Phase 3 · Price OCR (Gemini multimodal)

Playwright captures e-commerce pages; **Gemini Flash multimodal OCR** reads subsidies and checkout prices—not GPT.

### Phase 4 · Structured arbitration & export (OpenAI Structured Output)

**OpenAI** is used **only** for Strict JSON Schema output: conflict arbitration and Obsidian frontmatter alignment. It does **not** perform text reading or OCR.

### Dual-brain split (mandatory)

```
FastAPI event bus
    ├── Gemini 1.5 Flash   → Phase 1/2/3 massive text ingestion + screenshot OCR
    └── OpenAI gpt-4o(-mini) → Phase 4 Structured Output only (strict JSON / YAML)
```

Without API keys, the system falls back to a **keyword rules engine** so the mock flow still runs end-to-end.

---

## Architecture

| Layer | Stack | Role |
|-------|-------|------|
| Frontend | Streamlit + SSE | Streaming matrix, conflict badges, evidence cards |
| Backend | FastAPI + background threads | Task scheduling, SSE event push |
| Collectors | HTTP + DuckDuckGo search + URL injection | Best-effort official / video / forum / e-commerce fetch |
| Browser | Playwright (skeleton ready) | E-commerce long-page screenshots, captcha HITL pause |
| Output | Obsidian Markdown + Dataview | Durable local assets independent of the web UI |

---

## Current Status (2026-07)

### Done

- [x] **End-to-end mock pipeline** for Zeiss / Sony / Sigma 50mm lenses
- [x] **Four-phase pipeline + event bus** with `matrix_row_updated` for progressive UI refresh
- [x] **Hybrid ModelRouter**: Gemini for text detox/official specs/OCR; OpenAI **only** for Structured Output arbitration
- [x] **Real collector adapters**: search discovery, URL injection, HTML extraction, price parsing
- [x] **FastAPI**: `POST /tasks`, `GET /tasks/{id}/events` (SSE), `GET /result`
- [x] **Streamlit UI**: Phase 0 SKU picker, progressive table, 🟡/🔴 conflict badges, evidence links
- [x] **Obsidian writer**: Chinese dehydration reports + Dataview master matrix
- [x] **Unit tests**: Pipeline / RealCollector / ModelRouter / TaskManager / Checkpoint (10 passing)

### In progress / partial

- [~] **Live Gemini / OpenAI calls**: wired but require `.env` API keys
- [x] **Task checkpoint resume (Milestone 2 skeleton)**: memory/Redis checkpoints, `PAUSED_NEED_AUTH`, `POST /tasks/{id}/resume-auth`
- [x] **Playwright browser capture skeleton**: slice screenshots, captcha detection, session restore
- [x] **Streamlit HITL resume UI**: sidebar resume button
- [~] **Embedded browser window**: Milestone 2 tail item

### Not started / Milestone 3 in progress

- [x] **Bilibili / JD platform adapters**: comment snippets, JD script price parsing
- [x] **Gemini multi-slice OCR skeleton**: batch OCR via comma-separated screenshot paths
- [x] **Graceful degradation + diagnostics panel**: events, API `/diagnostics`, Streamlit panel
- [x] **Per-SKU fault isolation**: one failed SKU does not block the rest
- [~] **Live Gemini OCR**: requires API key + Playwright captures

---

## Roadmap

### Milestone 1 · Hybrid model routing (largely complete)

Run the FastAPI task pipeline; Gemini ingests large corpora and OCR screenshots; OpenAI Strict JSON locks output format.

### Milestone 2 · HITL checkpoint resume (largely complete)

1. ✅ Playwright session state save/restore
2. ✅ Pause on slider captcha + checkpoint persistence (memory/Redis)
3. ✅ API / Streamlit resume entrypoints
4. ⬜ Embedded browser window

### Milestone 3 · Production-grade collection (in progress)

1. ✅ Bilibili / JD adapters
2. ✅ Gemini multi-slice OCR + retry / tolerant JSON parsing
3. ✅ Graceful degradation, per-SKU isolation, diagnostics panel
4. ⬜ YouTube subtitles, context caching, multi-category schemas

### Milestone 4 · Knowledge-base enhancements

1. Obsidian templates and extended Dataview views
2. Historical price curves and evidence confidence scores
3. Optional CSV / Notion export

---

## Quick Start

### Requirements

- Python 3.12+
- (Optional) Playwright: `playwright install chromium`

### Install

```powershell
cd Specs-first
pip install -e .
```

### Mock demo (no API keys)

```powershell
python -m backend.pipeline
```

Output goes to `vault_output/` (gitignored; generated locally on run).

### Web UI

```powershell
streamlit run frontend/app.py
```

### API (SSE)

```powershell
uvicorn backend.api:app --reload
```

| Endpoint | Description |
|----------|-------------|
| `POST /discover` | Discover candidate SKUs |
| `POST /tasks` | Start a comparison task |
| `GET /tasks/{id}/events` | SSE live event stream |
| `GET /tasks/{id}/result` | Final matrix and vault paths |
| `POST /tasks/{id}/resume-auth` | Resume after manual auth |
| `GET /tasks/{id}/checkpoint` | Inspect paused checkpoint |
| `GET /tasks/{id}/diagnostics` | Collector degradation/error diagnostics |

### Enable real models

Copy `.env.example` to `.env`:

```env
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
DEFAULT_OPENAI_MODEL=gpt-4o-mini   # Structured Output only
DEFAULT_GEMINI_MODEL=gemini-1.5-flash  # text ingestion + OCR
OBSIDIAN_VAULT_PATH=./vault_output
SPECS_FIRST_MODE=mock
```

### Tests

```powershell
python -m unittest discover -s tests
```

---

## Repository Layout

```
Specs-first/
├── backend/          # Pipeline, API, dual-brain router, task runner
├── collectors/       # Mock / real collectors, HTTP, Playwright
├── frontend/         # Streamlit UI
├── obsidian/         # Vault writer
├── schemas/          # Data models and comparison matrix
├── tests/
├── plan.md           # Architecture plan v4.0 (Chinese)
├── README.md         # Chinese
└── README_EN.md      # English (this file)
```

---

## Obsidian Output Layout

```
vault_output/
├── 00_Specs_First_Matrix/
│   └── lens_progressive_comparison_matrix.md   # Dataview master view
└── 01_Product_Items/
    ├── zeiss_makro_planar_t_50mm_f_2.md
    ├── sony_fe_50mm_f1_2_gm.md
    └── sigma_50mm_f1_4_dg_dn_art.md
```

Enable the **Dataview** plugin in Obsidian and open the matrix file to render the comparison table locally—no web UI required.

---

## License

This project is licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE)

## Related Docs

- [中文 README](README.md)
- [Architecture plan v4.0](plan.md)
