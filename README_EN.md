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

Concurrent fetch from Bilibili subtitles/top comments, YouTube, Chiphell, Reddit, etc. **Gemini Flash** strips phrases like "legendary bokeh" and keeps only evidence-backed flaws.

> **Bilibili source scope (finalized)**: subtitles + top comments only — no danmaku collection. Native CC subtitles are preferred; when unavailable, the pipeline automatically falls back to downloading the audio (yt-dlp) and transcribing locally (funasr/faster-whisper). Disable via `BILIBILI_ASR_FALLBACK=false`.

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
| Collectors | HTTP + Playwright + AdapterRegistry | Official / video / forum / e-commerce fetch |
| Models | Gemini + OpenAI | Text detox/OCR + structured arbitration |
| Output | Obsidian Markdown + Dataview + CSV | Durable local assets independent of the web UI |

```
Streamlit UI
    └── frontend/api_client.py (in-process TestClient, shared task_manager)
            └── backend/task_runner.py
                    └── pipeline.py → candidate_processor.py
                            └── collectors/real.py
                                    ├── sources/ (official · video · forum · ecommerce · injection)
                                    └── adapters/registry.py → jd · bilibili · youtube · tmall_taobao
                            └── model_router.py (single instance injected into RealCollector)
                            └── obsidian/writer.py
```

**Layering notes:**

- `collectors/settings.py` owns environment config; `backend/config.py` re-exports for compatibility
- `AdapterRegistry` is wired at runtime (`for_url()` / `for_platform()`)
- `RealCollector` receives the pipeline's `router`; collectors no longer import `backend`

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
- [x] **Unit tests**: **88 passing** + GitHub Actions CI
- [x] **Taobao/Tmall adapter**: mtop H5 signing, `TAOBAO_COOKIE` config, captcha pause + resume
- [x] **AdapterRegistry runtime wiring** (`collectors/adapters/registry.py` → `sources/`)
- [x] **Collector dependency inversion**: `collectors/settings.py` + router injection; collectors do not import `backend`
- [x] **P0 health checks**: `GET /health`, `gemini_health`, `platform_health`, `scripts/smoke_platforms.py`
- [x] **P1 collection hardening**: DDG `ddgs` fallback, JD/Taobao cookie injection, mtop 3-layer retry
- [x] **Code modularization**: `candidate_processor`, split model routers, Pydantic API models, Streamlit `api_client`

### In progress / partial

- [~] **Live Gemini / OpenAI calls**: wired but require `.env` API keys
- [~] **Taobao/Tmall cookies**: expire periodically; re-copy from browser when API returns token errors
- [x] **Task checkpoint resume (Milestone 2 skeleton)**: memory/Redis checkpoints, `PAUSED_NEED_AUTH`, `POST /tasks/{id}/resume-auth`
- [x] **Playwright browser capture skeleton**: slice screenshots, captcha detection, session restore
- [x] **Streamlit HITL resume UI**: sidebar resume button
- [x] **Embedded browser window**: captcha screenshots + click/type commands relay through a `BrowserBridge`, driven live from inside the Streamlit page — no more alt-tabbing to a separate OS window

### Not started / Milestone 3 in progress

- [x] **Bilibili / JD platform adapters**: comment snippets, JD script price parsing
- [x] **Gemini multi-slice OCR skeleton**: batch OCR via comma-separated screenshot paths
- [x] **Graceful degradation + diagnostics panel**: events, API `/diagnostics`, Streamlit panel
- [x] **Per-SKU fault isolation**: one failed SKU does not block the rest
- [x] **YouTube subtitle adapter**: caption tracks from `ytInitialPlayerResponse`, review-oriented transcript snippets
- [x] **Bilibili ASR fallback**: local transcription when no native subtitle exists (subtitles + top comments only, no danmaku)
- [~] **Live Gemini OCR**: requires API key + Playwright captures

---

## Roadmap

### Milestone 1 · Hybrid model routing (largely complete)

Run the FastAPI task pipeline; Gemini ingests large corpora and OCR screenshots; OpenAI Strict JSON locks output format.

### Milestone 2 · HITL checkpoint resume (complete)

1. ✅ Playwright session state save/restore
2. ✅ Pause on slider captcha + checkpoint persistence (memory/Redis)
3. ✅ API / Streamlit resume entrypoints
4. ✅ Embedded browser window: `collectors/embedded_browser.py` relays screenshots/commands; the Streamlit task now runs on a background thread with polling rerenders so captcha screenshots and click/type controls live entirely inside the page

### Milestone 3 · Production-grade collection (largely complete)

1. ✅ Bilibili / JD adapters
2. ✅ Gemini multi-slice OCR + retry / tolerant JSON parsing
3. ✅ Graceful degradation, per-SKU isolation, diagnostics panel
4. ✅ Interference-resistant fetch (`page_sanitize` + `resilient_fetch` + browser main-content targeting)
5. ✅ YouTube subtitle adapter (`captionTracks` → transcript snippets)
6. ✅ YouTube comments API; Bilibili source scope finalized as subtitles + top comments (no danmaku), with ASR fallback
7. ✅ Gemini context caching: official-spec extraction and real-world dehydration reuse one cache per corpus, avoiding re-billing large text on retries (`GEMINI_CONTEXT_CACHE_*`)
8. ✅ Multi-category schema templates: `schemas/category_profile.py` ships 5-8 slot templates + bilingual (EN/ZH) label aliases for lens/phone/laptop/headphone/camera/monitor/keyboard/drone/wearable categories, so differently-worded labels collapse onto the same matrix column; unmodeled categories fall back to the generic 8-slot schema

### Milestone 4 · Knowledge-base enhancements

1. ✅ Obsidian templates and Dataview extension (including `evidence_confidence_avg`)
2. ⏸️ Historical price curves (frozen as a future optional item; not implemented in current release)
3. ✅ CSV export (`vault_output/00_Specs_First_Matrix/`); Notion sync remains optional and unimplemented

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
| `GET /tasks/{id}/events/snapshot` | Event snapshot (Streamlit polling) |
| `GET /tasks/{id}/result` | Final matrix and vault paths |
| `POST /tasks/{id}/resume-auth` | Resume after manual auth |
| `GET /tasks/{id}/checkpoint` | Inspect paused checkpoint |
| `GET /tasks/{id}/diagnostics` | Collector degradation/error diagnostics |
| `GET /tasks/{id}/browser/status` | Embedded browser session status (active/solved) |
| `GET /tasks/{id}/browser/screenshot` | Fetch latest embedded browser screenshot (base64) |
| `POST /tasks/{id}/browser/command` | Send click/type/key/scroll commands to the embedded browser |
| `GET /asr/status` | Check local ASR backend availability |
| `POST /asr/transcribe` | Manually trigger local audio transcription fallback |

### Enable real models

Copy `.env.example` to `.env`:

```env
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
DEFAULT_OPENAI_MODEL=gpt-4o-mini   # Structured Output only
DEFAULT_GEMINI_MODEL=gemini-2.5-flash  # text ingestion + OCR
OBSIDIAN_VAULT_PATH=./vault_output
SPECS_FIRST_MODE=mock

# Taobao/Tmall (real mode): copy full Cookie from browser DevTools while logged in
TAOBAO_COOKIE=
TAOBAO_M_H5_TK=
```

### Tests

```powershell
python -m unittest discover -s tests
```

**88 tests** currently passing.

---

## Repository Layout

```
Specs-first/
├── backend/          # Pipeline, API, dual-brain router, task runner, health
├── collectors/       # Mock/real collectors, settings, sources/, adapters/
├── scripts/          # smoke_platforms.py
├── frontend/         # Streamlit UI + api_client
├── obsidian/         # Vault writer + CSV export
├── schemas/          # Data models and comparison matrix
├── tests/            # 88 unit/integration tests
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
