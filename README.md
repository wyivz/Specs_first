# Specs-First · 不服跑个分

**[English](README_EN.md)** | 中文

> 别跟我扯情怀。把官方冰冷参数、民间真实翻车缺点、别人到手的最低价，横向排成一张带证据链的对比表。

Specs-First 是一款**反营销话术、证据优先**的商品对比系统。它自动从官网、B 站/YouTube、极客论坛、电商平台抓取信息，经 **Gemini 脱水 + OpenAI 仲裁** 双脑处理后，输出可流式查看的对比矩阵，并沉淀为本地 **Obsidian** 知识库资产。

---

## 解决什么问题？

| 痛点 | Specs-First 的做法 |
|------|-------------------|
| 参数虚标、营销词堆砌 | 只认官网/白皮书里的标称数据，作为 `official` 基准线 |
| 评测充値、难找大实话 | Gemini 当「毒舌质检员」，过滤吹捧，只留可溯源的翻车反馈 |
| 到手价迷雾 | 从电商页解析补贴/券后价，冲突格打角标并附证据链接 |
| 对比维度混乱 | 固定 5–8 个品类硬指标列 + `spec_highlights` 外挂属性桶 |

---

## 核心逻辑（四阶段流水线）

```
Phase 0 意图消歧 ──► Phase 1 权威骨架 ──► Phase 2 民间脱水 ──► Phase 3 价格/视觉 ──► Phase 4 冲突仲裁 & 落盘
  模糊词→勾选 SKU      官网/白皮书参数       Gemini 排毒            电商到手价              OpenAI 终审 + Obsidian
```

### Phase 0 · 意图消歧

用户输入如 `Zeiss 50mm 镜头`，系统发现最多 10 个候选 SKU，供勾选后再进入对比。

### Phase 1 · 权威骨架

定向检索品牌官网、说明书、白皮书，提取焦距、光圈、重量、镜片结构等**不可篡改的官方参数**。

### Phase 2 · 民间脱水

并发抓取 B 站弹幕/评论、YouTube、Chiphell、Reddit 等文本；**Gemini Flash** 过滤「德味十足」「大师之选」类废话，只保留带证据的实测缺点。

### Phase 3 · 价格归一

从京东/淘宝等页面解析标价、优惠券、补贴、跨店满减，计算**真实到手价**（后续将接入 Playwright 截图 + Vision OCR）。

### Phase 4 · 冲突仲裁 & 资产沉淀

**OpenAI** 用 Strict JSON 对齐官方数据与民间黑料，输出仲裁结论；结果写入 Obsidian Vault（一 SKU 一文件 + Dataview 主视图矩阵）。

### 双脑分工

```
FastAPI 事件总线
    ├── Gemini 1.5 Flash  → Phase 1/2 大吞吐文本清洗
    └── OpenAI gpt-4o(-mini) → Phase 3/4 视觉提取 & 结构化终审
```

未配置 API Key 时，自动降级为**关键词规则引擎**，mock 流程仍可完整跑通。

---

## 架构一览

| 层级 | 技术 | 职责 |
|------|------|------|
| 前端 | Streamlit + SSE | 流式对比表、翻车角标、证据卡片 |
| 后端 | FastAPI + 后台线程 | 任务调度、SSE 事件推送 |
| 采集 | HTTP + DuckDuckGo 搜索 + URL 注入 | 官网 / 视频 / 论坛 / 电商 best-effort 抓取 |
| 浏览器 | Playwright（骨架已备） | 电商长图截图、验证码 HITL 挂起 |
| 输出 | Obsidian Markdown + Dataview | 本地永久资产，脱离 Web 仍可查看 |

---

## 目前进展（2026-07）

### 已完成

- [x] **端到端 Mock 流水线**：Zeiss / Sony / Sigma 三款 50mm 镜头默认可跑
- [x] **四阶段 Pipeline + 事件总线**：`matrix_row_updated` 等事件支持流式刷新
- [x] **双脑 ModelRouter 骨架**：Gemini 脱水 + OpenAI Strict JSON 仲裁 + 关键词降级
- [x] **Real Collector 适配层**：搜索发现、URL 定点注入、HTML 抽取、价格解析
- [x] **FastAPI**：`POST /tasks`、`GET /tasks/{id}/events`（SSE）、`GET /result`
- [x] **Streamlit UI**：Phase 0 选 SKU、渐进式对比表、🟡/🔴 冲突角标、证据链
- [x] **Obsidian Writer**：中文脱水报告 + Dataview 动态矩阵
- [x] **单元测试**：Pipeline / RealCollector / ModelRouter / TaskManager / Checkpoint（10 项通过）

### 进行中 / 部分完成

- [~] **Gemini / OpenAI 实调**：接口已接，需配置 `.env` 中的 API Key 方可启用
- [x] **任务断点续传（Milestone 2 骨架）**：内存/Redis Checkpoint、`PAUSED_NEED_AUTH` 挂起、`POST /tasks/{id}/resume-auth` 续传
- [x] **Playwright 浏览器采集骨架**：电商页截图切片、验证码检测、Session 状态文件恢复
- [~] **Streamlit HITL 续传 UI**：侧边栏「续传任务」已接，完整浏览器弹窗待完善

### 尚未开始

- [ ] 电商页 Vision OCR 真实到手价（gpt-4o 多模态）
- [ ] 验证码 HITL：任务 `PAUSED_NEED_AUTH` → 前端弹窗 → Session 续传
- [ ] B 站/YouTube 字幕与弹幕专用解析器
- [ ] Context Caching 降低 Gemini 长文成本
- [ ] 多品类 JIT Schema（相机机身、耳机等）

---

## 后续目标

### Milestone 1 · 混合模型路由（已基本完成）

跑通 FastAPI 任务管道；Gemini 吞大文本，OpenAI Strict JSON 写出不乱码的 SKU Markdown。

### Milestone 2 · 人机协同断点续传（进行中）

1. ✅ Playwright Session 状态文件保存/恢复
2. ✅ 遇滑块时任务挂起 + Checkpoint 持久化（内存/Redis）
3. ✅ API / Streamlit 续传入口
4. ⬜ 前端嵌入式浏览器窗口与 Worker 队列

### Milestone 3 · 生产级采集

1. 各平台专用 Adapter（B 站 API/页面、京东 Mobile 页）
2. 价格截图 + Vision 切片 OCR
3. 采集失败降级策略与诊断面板

### Milestone 4 · 知识库增强

1. Obsidian 模板与 Dataview 图表扩展
2. 历史价格曲线、证据置信度字段
3. 导出 CSV / Notion 同步（可选）

---

## 快速开始

### 环境要求

- Python 3.12+
- （可选）Playwright：`playwright install chromium`

### 安装

```powershell
cd Specs-first
pip install -e .
```

### Mock 演示（无需 API Key）

```powershell
python -m backend.pipeline
```

生成文件默认写入 `vault_output/`（已在 `.gitignore` 中，本地运行后自动生成）。

### Web UI

```powershell
streamlit run frontend/app.py
```

### API（SSE）

```powershell
uvicorn backend.api:app --reload
```

| 端点 | 说明 |
|------|------|
| `POST /discover` | 发现候选 SKU |
| `POST /tasks` | 启动对比任务 |
| `GET /tasks/{id}/events` | SSE 实时事件流 |
| `GET /tasks/{id}/result` | 最终矩阵与 Vault 路径 |
| `POST /tasks/{id}/resume-auth` | 验证码通过后续传任务 |
| `GET /tasks/{id}/checkpoint` | 查看挂起任务断点 |

### 配置真实模型

复制 `.env.example` 为 `.env`：

```env
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
DEFAULT_OPENAI_MODEL=gpt-4o-mini
DEFAULT_GEMINI_MODEL=gemini-1.5-flash
OBSIDIAN_VAULT_PATH=./vault_output
SPECS_FIRST_MODE=mock
```

### 测试

```powershell
python -m unittest discover -s tests
```

---

## 目录结构

```
Specs-first/
├── backend/          # Pipeline、API、双脑路由、任务调度
├── collectors/       # Mock / Real 采集器、HTTP、Playwright
├── frontend/         # Streamlit UI
├── obsidian/         # Vault 写入器
├── schemas/          # 数据模型与对比矩阵
├── tests/
├── plan.md           # 详细架构计划书 v4.0
├── README.md         # 中文（本文件）
└── README_EN.md      # English
```

---

## Obsidian 输出示例

```
vault_output/
├── 00_Specs_First_Matrix/
│   └── lens_progressive_comparison_matrix.md   # Dataview 主视图
└── 01_Product_Items/
    ├── zeiss_makro_planar_t_50mm_f_2.md
    ├── sony_fe_50mm_f1_2_gm.md
    └── sigma_50mm_f1_4_dg_dn_art.md
```

在 Obsidian 中启用 **Dataview** 插件，打开矩阵文件即可本地渲染横向对比表，无需依赖本 Web 系统。

---

## 许可证

MIT — 详见 [LICENSE](LICENSE)

## 相关文档

- [English README](README_EN.md)
- [架构计划书 v4.0](plan.md)
