# Specs-First · 不服跑个分

**[English](README_EN.md)** | 中文

> 别跟我扯情怀。把官方冰冷参数、民间真实翻车缺点、别人到手的最低价，横向排成一张带证据链的对比表。

Specs-First 是一款**反营销话术、证据优先**的商品对比系统。它从官网、B 站/YouTube、极客论坛、电商平台抓取信息，经 **Gemini 脱水 + OpenAI 结构化仲裁** 处理后，输出可流式查看的对比矩阵，并沉淀为本地 **Obsidian** 知识库资产。

**品类不限预设模板**：勾选 SKU 后，Gemini 识图梳理参数线索 → ChatGPT Structured Outputs 锁定该次对比的 5–8 个硬指标与关键词，任意品类共用同一套 JIT Schema。

---

## 解决什么问题？

| 痛点 | Specs-First 的做法 |
|------|-------------------|
| 参数虚标、营销词堆砌 | 只认官网/白皮书标称数据，作为 `official` 基准线 |
| 评测充值、难找大实话 | Gemini 过滤吹捧，只留可溯源的翻车反馈 |
| 到手价迷雾 | 解析补贴/券后价；冲突格打角标并附证据链接 |
| 对比维度混乱 | **JIT 动态 5–8 硬指标列**（识图 → 建表）+ `spec_highlights` 外挂属性桶 |

---

## 核心流水线

```
Phase 0 发现 SKU
    │  勾选后 ──► 0.5 JIT 建表（Gemini 识图 → ChatGPT 锁槽）
    ▼
Phase 1 官方规格 ──► Phase 2 民间脱水 ──► Phase 3 到手价/OCR ──► Phase 4 冲突仲裁 & Obsidian
```

| 阶段 | 做什么 | 模型角色 |
|------|--------|----------|
| 0 | 发现最多 10 个候选 SKU，用户勾选 | — |
| 0.5 | 探针采详情图 → 生成品类标签、硬指标槽、别名与检索词 | Gemini 识图 + OpenAI 建表 |
| 1 | 官网 + 电商参数页填槽 | Gemini 文本/识图填槽 |
| 2 | B 站/YouTube/论坛真实反馈脱水 | Gemini 排毒 |
| 3 | 电商到手价 + 截图 OCR | Gemini OCR |
| 4 | 冲突仲裁，写入 Obsidian + CSV | OpenAI Structured Outputs |

无 API Key 时自动降级为**关键词规则引擎**（槽位回退 `parameter_a…h`）；Mock 模式无需联网即可演示。

---

## 架构一览

| 层级 | 技术 | 职责 |
|------|------|------|
| 前端 | Streamlit（输入 / 运行状态 / 输出三栏） | 选 SKU、Health、流式矩阵、JIT Schema、证据卡、嵌入式验证码浏览器 |
| 后端 | FastAPI + 后台线程 | 任务调度、事件推送、断点续传 |
| 采集 | HTTP + Playwright + AdapterRegistry | 官网 / 视频 / 论坛 / 京东 / 淘宝天猫 |
| 模型 | Gemini + OpenAI | 识图/脱水/OCR + JIT 建表与仲裁 |
| 输出 | Obsidian + Dataview + CSV | 本地永久资产 |

```
Streamlit UI（frontend/app.py）
    ├── frontend/ui/input_panel.py      # 输入与侧边栏配置
    ├── frontend/ui/status_panel.py     # 运行状态（st.fragment 局部刷新）
    ├── frontend/ui/output_panel.py     # 矩阵与导出
    ├── frontend/event_listener.py      # EventBus 后台订阅
    └── frontend/api_client.py
            └── backend/task_runner.py
                    └── pipeline.py（含 JIT schema bootstrap）
                            ├── candidate_processor.py
                            ├── collectors/real.py → sources/ + adapters/
                            ├── model_router（keyword | hybrid）
                            └── obsidian/writer.py
```

- 配置在 `collectors/settings.py`；`backend/config.py` 仅兼容 re-export
- `RealCollector` 接收注入的 `router` 与 `DynamicCategoryProfile`
- 采集层不依赖 `backend` 包

---

## 目前进展（2026-07）

### 已完成

- [x] 端到端 Mock / Real 流水线，单 SKU 故障隔离
- [x] 四阶段 Pipeline + **JIT 品类 Schema**（无预设镜头/手机等模板）
- [x] 双脑 ModelRouter（`router_keyword` / `router_hybrid` / `router_schemas`）
- [x] FastAPI + Streamlit（`api_client`）+ 事件流式刷新
- [x] 验证码 HITL：`PAUSED_NEED_AUTH` + 嵌入式浏览器 + 续传
- [x] 平台 Adapter：京东、B 站、YouTube、淘宝/天猫（mtop + Cookie）
- [x] AdapterRegistry 运行时接线；采集层依赖倒置
- [x] Obsidian + Dataview + CSV
- [x] 健康检查：`GET /health`、`scripts/smoke_platforms.py`
- [x] **单元测试 138 项** + GitHub Actions CI

### 需本机实调

- [~] Gemini / OpenAI：配置 `.env` 后 Real 模式实测 JIT 建表与仲裁
- [~] 淘宝/京东 Cookie：会过期，需从浏览器重新复制

### 暂缓

- ⏸️ 历史价格曲线、Notion 同步

---

## 快速开始

### 环境

- Python 3.12+
- Real 模式：`playwright install chromium`

### 安装

```powershell
cd Specs-first
pip install fastapi uvicorn streamlit httpx openai google-generativeai redis playwright
pip install -e .
```

### Mock 演示（无需 API Key）

```powershell
python -m backend.pipeline
```

输出默认写入 `vault_output/`。

### Web UI（推荐）

```powershell
streamlit run frontend/app.py
```

界面分为三个区域：

| 区域 | 内容 |
|------|------|
| **输入** | 对比查询、品类提示、SKU 发现/勾选、侧边栏运行配置（mock/real、Playwright、Source URLs） |
| **运行状态** | 平台 Health、进度条、阶段 pill、实时事件流、采集诊断、验证码嵌入式浏览器 |
| **输出** | 渐进式对比矩阵、证据卡、Obsidian 路径、CSV 下载 |

- 侧边栏选 `mock` 或 `real`；Real 建议勾选 Playwright
- 任务进行中通过局部刷新（`st.fragment`）更新状态，输入框不会整页重刷丢焦点
- 遇验证码挂起时，侧边栏点击「续传任务」

### API（可选）

```powershell
uvicorn backend.api:app --reload
```

| 端点 | 说明 |
|------|------|
| `GET /health` | 配置与凭证健康检查 |
| `POST /discover` | 发现候选 SKU |
| `POST /tasks` | 启动对比（含 JIT 建表） |
| `GET /tasks/{id}/events` | SSE 事件流（含 `category_profile_ready`） |
| `GET /tasks/{id}/result` | 矩阵与 Vault 路径 |
| `POST /tasks/{id}/resume-auth` | 验证码续传 |
| `GET /tasks/{id}/diagnostics` | 采集诊断 |
| `GET /asr/status` · `POST /asr/transcribe` | 本地 ASR |

### 测试

```powershell
python -m unittest discover -s tests
```

当前 **175** 项单元测试通过（不含 live smoke）。

```powershell
python scripts/smoke_platforms.py --health-only
```

---

## Real 模式配置

复制 `.env.example` → `.env`：

```env
GEMINI_API_KEY=...
OPENAI_API_KEY=...
DEFAULT_GEMINI_MODEL=gemini-3.5-flash
DEFAULT_OPENAI_MODEL=gpt-4o-mini
OBSIDIAN_VAULT_PATH=./vault_output
```

| 平台 | 变量 | 说明 |
|------|------|------|
| B 站 | `BILIBILI_SESSDATA` 等 | 字幕 + 热评 |
| 淘宝/天猫 | `TAOBAO_COOKIE` | 参数/到手价；含 `_m_h5_tk` 更稳 |
| 京东 | `JD_COOKIE` | 建议大陆网络；`pt_key` / `pt_pin` |
| Reddit（可选） | `REDDIT_COOKIE` | 未配置则跳过 Reddit 搜索 |

**推荐流程：** Streamlit 选 `real` → 勾选 Playwright → 品类提示可留空 →「开始对比」→ 遇验证码在页内浏览器完成后续传。Source URLs 可选；有直链更稳。

### 跑通验收清单（Real）

目标不是「全平台全满」，而是：**矩阵有槽 + 至少一侧价或规格 + 若干条证据**。

1. **配齐 P0 凭证**（写入 `.env`，勿提交）：
   - `GEMINI_API_KEY` + `OPENAI_API_KEY` + `SPECS_FIRST_MODE=real`
   - `JD_COOKIE`；`TAOBAO_COOKIE` + `TAOBAO_M_H5_TK`；B 站 `SESSDATA` / `BILI_JCT` / `DEDEUSERID`
   - 可选：`YOUTUBE_COOKIE`、`REDDIT_COOKIE`
2. **先跑 smoke**（哪段红修哪段）：
   ```powershell
   python scripts/smoke_platforms.py --probe-gemini
   ```
3. **注入直链保底发现**（绕过 DDG 单点）：侧边栏 Source URLs 或 `.env` 的 `OPTIONAL_SOURCE_URLS` 各贴  
   1 个京东商品、1 个淘宝/天猫商品、1–2 个真实评测 BV/YouTube（勿用镇站之宝）。
4. Streamlit：`real` + Playwright；输入明确型号后开始对比。
5. 采集节奏：电商默认约 3s 间隔 + 抖动；京东频控后自动退避，勿对 `pc-frequent-pro` 反复开浏览器。

大陆网络访问京东/淘宝更稳；Cookie 用日常登录浏览器复制，过期即换。

---

## 目录结构

```
Specs-first/
├── backend/           # pipeline（含 JIT bootstrap）、API、router、task_runner
├── collectors/        # settings、real/mock、sources/、adapters/
├── frontend/          # Streamlit + api_client
├── schemas/           # 模型 + DynamicCategoryProfile + matrix
├── obsidian/          # Vault 写入 + CSV
├── scripts/           # smoke_platforms.py
├── tests/             # 138 项测试
├── plan.md            # 架构计划
└── .github/workflows/ # CI
```

---

## Obsidian 输出

```
vault_output/
├── 00_Specs_First_Matrix/
│   ├── *_comparison_matrix.md
│   └── *.csv
└── 01_Product_Items/
    └── <sku>.md
```

在 Obsidian 启用 **Dataview** 即可查看对比矩阵。

---

## 后续建议

| 优先级 | 事项 |
|--------|------|
| P0 | 本机 Real 跑通：`.env` + Playwright + Cookie |
| P1 | JIT 建表 / 仲裁 prompt 调优 |
| P2 | B 站 WBI、YouTube 字幕、Reddit 证据加深 |
| 上云时 | API 分离、CORS/限流、Redis、Docker |

---

## 许可证

**GNU GPL v3.0** — 见 [LICENSE](LICENSE)

## 相关文档

- [English README](README_EN.md)
- [架构计划书](plan.md)
