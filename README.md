# Specs-First · 不服跑个分

**[English](README_EN.md)** | 中文

> 别跟我扯情怀。把官方冰冷参数、民间真实翻车缺点、别人到手的最低价，横向排成一张带证据链的对比表。

Specs-First 是一款**反营销话术、证据优先**的商品对比系统。它自动从官网、B 站/YouTube、极客论坛、电商平台抓取信息，经 **Gemini 脱水 + OpenAI 仲裁** 双脑处理后，输出可流式查看的对比矩阵，并沉淀为本地 **Obsidian** 知识库资产。

---

## 解决什么问题？

| 痛点 | Specs-First 的做法 |
|------|-------------------|
| 参数虚标、营销词堆砌 | 只认官网/白皮书里的标称数据，作为 `official` 基准线 |
| 评测充值、难找大实话 | Gemini 过滤吹捧，只留可溯源的翻车反馈 |
| 到手价迷雾 | 从电商页解析补贴/券后价，冲突格打角标并附证据链接 |
| 对比维度混乱 | 固定 5–8 个品类硬指标列 + `spec_highlights` 外挂属性桶 |

---

## 核心逻辑（四阶段流水线）

```
Phase 0 意图消歧 ──► Phase 1 权威骨架 ──► Phase 2 民间脱水 ──► Phase 3 价格/视觉 ──► Phase 4 冲突仲裁 & 落盘
  模糊词→勾选 SKU      官网/电商参数           Gemini 排毒            电商到手价              OpenAI 终审 + Obsidian
```

| 阶段 | 做什么 |
|------|--------|
| Phase 0 | 发现最多 10 个候选 SKU，勾选后进入对比 |
| Phase 1 | 官网 + 电商参数页提取官方规格 |
| Phase 2 | B 站字幕/评论、YouTube、论坛等真实反馈脱水 |
| Phase 3 | 电商到手价解析 + Gemini 截图 OCR |
| Phase 4 | OpenAI 结构化仲裁，写入 Obsidian + CSV |

未配置 API Key 时自动降级为**关键词规则引擎**；Mock 模式无需联网即可跑通演示。

---

## 架构一览

| 层级 | 技术 | 职责 |
|------|------|------|
| 前端 | Streamlit | 选 SKU、流式对比表、证据卡片、嵌入式验证码浏览器 |
| 后端 | FastAPI + 后台线程 | 任务调度、事件推送 |
| 采集 | HTTP + Playwright + AdapterRegistry | 官网 / 视频 / 论坛 / 京东 / 淘宝天猫 |
| 模型 | Gemini + OpenAI | 文本脱水/OCR + 结构化仲裁 |
| 输出 | Obsidian Markdown + Dataview + CSV | 本地永久资产 |

```
Streamlit UI
    └── frontend/api_client.py（进程内 TestClient，共享 task_manager）
            └── backend/task_runner.py
                    └── pipeline.py → candidate_processor.py
                            └── collectors/real.py
                                    ├── sources/（official · video · forum · ecommerce · injection）
                                    └── adapters/registry.py → jd · bilibili · youtube · tmall_taobao
                            └── model_router.py（单实例注入 RealCollector）
                            └── obsidian/writer.py
```

**分层要点：**

- `collectors/settings.py` 承载环境配置；`backend/config.py` 仅做兼容 re-export
- `AdapterRegistry` 已在运行时接线（`registry.for_url()` / `for_platform()`）
- `RealCollector` 接收 pipeline 注入的 `router`，采集层不再 import `backend`

---

## 目前进展（2026-07）

### 已完成

- [x] 端到端 Mock / Real 流水线，单 SKU 故障隔离
- [x] 四阶段 Pipeline + 事件总线（`matrix_row_updated` 流式刷新）
- [x] 双脑 ModelRouter（已拆分为 `router_keyword` / `router_hybrid` / `router_schemas`）
- [x] 单 SKU 处理模块 `backend/candidate_processor.py`
- [x] FastAPI + Pydantic 请求模型（`backend/api_models.py`）
- [x] Streamlit 统一经 `frontend/api_client.py` 调用 API
- [x] 验证码 HITL：任务挂起 `PAUSED_NEED_AUTH` + 嵌入式浏览器 + 续传
- [x] 平台 Adapter：京东、B 站、YouTube、**淘宝/天猫（mtop 签名 + Cookie）**
- [x] **AdapterRegistry 运行时接线**（`collectors/adapters/registry.py` → `sources/`）
- [x] **采集层依赖倒置**：`collectors/settings.py` + router 注入，采集层不依赖 `backend`
- [x] Obsidian + Dataview + CSV 导出
- [x] **P0 健康检查**：`GET /health`、`gemini_health`、`platform_health`、`scripts/smoke_platforms.py`
- [x] **P1 采集加固**：DDG `ddgs` 回退、JD/淘宝 Cookie 注入、mtop 三层回退
- [x] **单元测试 88 项** + GitHub Actions CI（`.github/workflows/test.yml`）

### 部分完成

- [~] **Gemini / OpenAI 实调**：需配置 `.env` API Key；建议本机 Real 模式实测
- [~] **Gemini OCR 实调**：需 API Key + Playwright 截图
- [~] **淘宝/天猫**：已支持 Cookie 配置 + mtop 签名 + 验证码暂停；Cookie 会过期需手动更新

### 已冻结 / 未做

- ⏸️ 历史价格曲线
- ⏸️ Notion 同步（可选，未实现）

---

## 快速开始（本机）

### 环境要求

- Python 3.12+
- Playwright（Real 模式）：`playwright install chromium`

### 安装

```powershell
cd Specs-first
pip install fastapi uvicorn streamlit httpx openai google-generativeai redis playwright
pip install -e .
```

> 若 `pip install -e .` 报错，可仅安装上面列出的依赖，在项目根目录直接运行即可。

### Mock 演示（无需 API Key）

```powershell
python -m backend.pipeline
```

输出默认写入 `vault_output/`。

### Web UI（推荐）

```powershell
streamlit run frontend/app.py
```

侧边栏选择 `Collector mode`：`mock`（演示）或 `real`（真实采集）。

### API 服务（可选单独启动）

```powershell
uvicorn backend.api:app --reload
```

| 端点 | 说明 |
|------|------|
| `GET /health` | 配置与凭证健康检查（Gemini 模型、Cookie 等） |
| `POST /discover` | 发现候选 SKU |
| `POST /tasks` | 启动对比任务 |
| `GET /tasks/{id}` | 任务状态 |
| `GET /tasks/{id}/events` | SSE 实时事件流 |
| `GET /tasks/{id}/events/snapshot` | 事件快照（Streamlit 轮询用） |
| `GET /tasks/{id}/result` | 最终矩阵与 Vault 路径 |
| `POST /tasks/{id}/resume-auth` | 验证码通过后续传 |
| `GET /tasks/{id}/checkpoint` | 查看挂起断点 |
| `GET /tasks/{id}/diagnostics` | 采集降级/错误诊断 |
| `GET /tasks/{id}/browser/*` | 嵌入式浏览器控制 |
| `GET /asr/status` · `POST /asr/transcribe` | 本地 ASR 转写 |

### 测试

```powershell
python -m unittest discover -s tests
```

### 平台可用性冒烟（Real 模式）

检查 Gemini 配置、各平台 Cookie，并对京东/淘宝/B 站/YouTube/DuckDuckGo 做最小 live 探测：

```powershell
python scripts/smoke_platforms.py
python scripts/smoke_platforms.py --probe-gemini --output vault_output/smoke_report.json
python scripts/smoke_platforms.py --health-only
```

报告默认写入 `vault_output/smoke_report.json`。未配置 Cookie 的平台会标记为 `skip`，不算失败。

当前：**88 项单元测试全部通过**（不含 live smoke）。

---

## 本机 Real 模式配置

复制 `.env.example` 为 `.env`，按需填写：

### 1. 模型（提升结论质量）

```env
GEMINI_API_KEY=...
OPENAI_API_KEY=...
DEFAULT_GEMINI_MODEL=gemini-2.5-flash
DEFAULT_OPENAI_MODEL=gpt-4o-mini
OBSIDIAN_VAULT_PATH=./vault_output
```

### 2. B 站（字幕 + 热门评论）

从浏览器 DevTools → Application → Cookies → bilibili.com 复制：

```env
BILIBILI_SESSDATA=...
BILIBILI_BILI_JCT=...
BILIBILI_DEDEUSERID=...
BILIBILI_BUVID3=...
```

### 3. 淘宝 / 天猫（商品参数与到手价）

登录 taobao.com 或 tmall.com 后，复制完整 Cookie 字符串（建议包含 `_m_h5_tk`、`_m_h5_tk_enc`、`cna`、`isg`）：

```env
TAOBAO_COOKIE=_m_h5_tk=你的token_时间戳; cookie2=...; t=...; ...
```

或仅填签名 token（推荐仍配置完整 `TAOBAO_COOKIE`）：

```env
TAOBAO_M_H5_TK=你的token_时间戳
```

系统会：

1. 请求商品页时自动携带 Cookie
2. 对 `mtop.taobao.detail.getdesc` / `getdetail` 等 API 计算签名；HTTP 失败时回退到 Playwright 浏览器内 `fetch`
3. `_m_h5_tk` 过期时自动刷新 token 并重签一次
4. token 过期或触发风控时任务挂起 → 在 Streamlit 嵌入式浏览器完成验证 → 侧边栏「续传任务」

### 4. 京东（到手价 / 规格）

登录 jd.com 后复制 Cookie（至少 `pt_key`、`pt_pin`）。**建议使用大陆网络**。

```env
JD_COOKIE=pt_key=...; pt_pin=...; __jda=...; ...
```

系统会在 HTTP 与 Playwright 请求中自动注入 JD Cookie；价格仍可能需浏览器渲染后解析。

### 5. Reddit（可选，论坛证据）

登录 reddit.com 后从 DevTools 复制 Cookie（通常含 `reddit_session`、`token_v2`）：

```env
REDDIT_COOKIE=reddit_session=...; token_v2=...
```

| 配置状态 | 行为 |
|----------|------|
| 未配置 | 自动 `site:reddit.com` 搜索**跳过**（不空跑） |
| 已配置 | 论坛搜索包含 Reddit，且 Reddit URL 自动走 Playwright + Cookie |
| 任意状态 | **Source URLs** 粘贴 Reddit 帖子链接仍可用（建议勾选 Playwright） |

Cookie 过期后需重新复制；遇登录墙可走嵌入式浏览器续传。

### 6. 推荐使用方式

| 步骤 | 操作 |
|------|------|
| 1 | Streamlit 选 `real`，勾选「启用 Playwright」 |
| 2 | 在 **Source URLs** 粘贴商品链接（每行一个） |
| 3 | 选品类（镜头/手机/键盘等） |
| 4 | 点击「开始对比」 |
| 5 | 遇验证码在页面内浏览器完成验证后续传 |

> **提示**：贴链接比自动搜索更稳；Cookie 过期后需重新从浏览器复制更新。

---

## 目录结构

```
Specs-first/
├── backend/
│   ├── pipeline.py              # 流水线编排
│   ├── candidate_processor.py   # 单 SKU 四阶段处理
│   ├── api.py / api_models.py   # FastAPI + Pydantic
│   ├── config.py                # re-export collectors/settings
│   ├── gemini_health.py         # Gemini 模型退役检测
│   ├── platform_health.py       # /health 配置聚合
│   ├── model_router.py          # 路由工厂
│   ├── router_keyword.py        # 关键词降级
│   ├── router_hybrid.py         # Gemini + OpenAI
│   └── task_runner.py           # 任务线程调度
├── collectors/
│   ├── settings.py              # 环境配置（采集层独立）
│   ├── protocols.py             # SpecExtractionRouter 协议
│   ├── real.py / mock.py
│   ├── sources/                 # 按来源类型拆分
│   │   ├── official.py
│   │   ├── video.py
│   │   ├── forum.py
│   │   ├── ecommerce.py
│   │   └── injection.py
│   └── adapters/                # 京东/B站/YouTube/淘宝天猫 + registry.py
├── scripts/
│   └── smoke_platforms.py       # 平台冒烟探测
├── frontend/
│   ├── app.py                   # Streamlit UI
│   └── api_client.py            # 进程内 TestClient
├── obsidian/                    # Vault 写入 + CSV
├── schemas/                     # 数据模型 + 品类模板
├── tests/                       # 88 项单元/集成测试
└── .github/workflows/test.yml   # CI
```

---

## Obsidian 输出

```
vault_output/
├── 00_Specs_First_Matrix/
│   ├── *_comparison_matrix.md    # Dataview 主视图
│   └── *.csv                       # CSV 导出
└── 01_Product_Items/
    └── <sku>.md                    # 单品脱水报告
```

在 Obsidian 启用 **Dataview** 插件即可本地查看对比矩阵。

---

## 后续建议（本机优先）

当前定位：**先在本机真实使用，云端部署以后再做**。

### 近期建议

| 优先级 | 事项 | 说明 |
|--------|------|------|
| P0 | 本机 Real 跑通 | 配好 `.env`、Playwright、贴商品 URL，实测常比品类 |
| P0 | 维护淘宝 Cookie | 过期后重新复制；失败看诊断区 |
| P1 | Gemini/OpenAI 实调 | 配 Key 后对比关键词降级效果 |
| P1 | 常用品类别名补充 | `schemas/category_profile.py` |
| P2 | B 站启动探测 + health 增强 | 降低 WBI 403 |
| P2 | YouTube PoToken / InnerTube | 恢复字幕证据 |
| P2 | Reddit 评论结构化解析（`RedditAdapter`） | 在 Cookie 启用后提升证据质量 |

### 上云时再考虑

| 事项 | 说明 |
|------|------|
| API 远程地址模式 | `SPECS_FIRST_API_URL` 分离 Streamlit 与 API 容器 |
| CORS / API Key / 限流 | 公网暴露时需要 |
| Redis 任务状态外置 | 重启恢复、多实例 |
| Docker / 部署文档 | 容器化一键启动 |
| 任务队列（ARQ/RQ） | 多用户并发时 |

### 可暂缓

- 全链路 async 改造
- Notion 同步、历史价格曲线
- Kafka 级消息队列

---

## 许可证

**GNU General Public License v3.0** — 详见 [LICENSE](LICENSE)

## 相关文档

- [English README](README_EN.md)
- [架构计划书 v4.0](plan.md)
