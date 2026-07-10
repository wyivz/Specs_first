# Specs-First 交接文档

> 更新时间：2026-07-10（本地会话）  
> 仓库：https://github.com/wyivz/Specs_first  
> 远程最新提交：`39afbba` — `feat(platform): Reddit opt-in, Taobao captcha UX, live run script`  
> **注意：下方「本机未提交改动」大量存在，尚未 push。**

---

## 1. 终端 / 后台进程（已处理）

会话期间 Cursor 后台曾挂起 **3 个 unittest 全量/半全量测试**，运行超过 30 分钟无输出，判定为**卡死**（非正常长跑）：

| PID | 命令 | 状态 |
|-----|------|------|
| 21328 → python 24700 | `python -m unittest discover -s tests -q` | 全量套件卡住 |
| 14364 → python 14308 | `unittest discover -p test_*.py -v` | 全量套件卡住 |
| 8500 → python 42696 | 卡在 `test_real_collector.py` | MockCollector 网络/等待类测试挂起 |

**处理：** 上述 python/powershell 进程已在 2026-07-10 交接时 `Stop-Process` 终止。

**以后跑测试建议：**

```powershell
# 快测（推荐日常）
python -m unittest discover -s tests -p "test_adapters.py" -q
python -m unittest discover -s tests -p "test_youtube*.py" -q
python -m unittest discover -s tests -p "test_collection_trace_and_guards.py" -q

# 避免一次性 discover 全 tests（含 test_api / test_real_collector 易挂）
```

---

## 2. 项目目标（一句话）

**证据优先的商品对比流水线**：官方参数 + 民间评测/翻车证据 + 真实到手价 → Gemini/OpenAI 仲裁 → Obsidian 输出。

---

## 3. 架构（已落地）

```
Streamlit/API → backend/pipeline.py → RealCollector / MockCollector
                      ↓
    collectors/sources/  (official · video · forum · ecommerce · injection)
                      ↓
    collectors/adapters/registry.py  → jd · tmall_taobao · bilibili · youtube
                      ↓
    collectors/resilient_fetch.py + collectors/http.py + collectors/browser.py
```

- **配置源**：`collectors/settings.py`（`backend/config.py` 再导出）
- **Real 模式**：按 **SKU 自动搜索**（DDG + 平台 adapter），`Source URLs` 仅为可选补充
- **依赖方向**：collectors 不依赖 backend（router 注入）

---

## 4. 本机未提交改动（相对 `39afbba`）

### 4.1 采集日志（Collection Trace）

| 文件 | 说明 |
|------|------|
| `collectors/collection_trace.py` | 人类可读文本日志 + diagnostics |
| `collectors/resilient_fetch.py` | 每次 fetch 写 trace |
| `collectors/real.py` | 按 task_id 生成 `vault_output/collection_trace_<task>.log` |
| `collectors/sources/ecommerce.py` | 价格/搜索日志 |
| `collectors/sources/injection.py` | 注入 URL 日志 + JD mgets 价 |

环境变量：`COLLECTION_TRACE=true`，`COLLECTION_TRACE_DIR=./vault_output`

### 4.2 JD 价格修复

| 文件 | 说明 |
|------|------|
| `collectors/adapters/jd.py` | 优先 `p.3.cn/prices/mgets`；HTML 优先到手价/券后价，避免取噪声低价（如 ¥116） |

### 4.3 Bilibili

| 文件 | 说明 |
|------|------|
| `collectors/adapters/bilibili_guard.py` | 拦截占位 BV `BV1GJ411x7h7`（镇站之宝 Rick Roll） |
| `collectors/adapters/bilibili.py` / `bilibili_api_client.py` | 守卫 + 标题检测 |

**说明：** Rick Roll 曾是 smoke 脚本占位符，不是 B 站攻击。正式流程应靠 `video_search_queries(sku)` 自动搜评测。

### 4.4 YouTube 字幕（含 PoToken 缓解）

| 文件 | 说明 |
|------|------|
| `collectors/adapters/youtube.py` | 多层回退：HTTP timedtext → **Playwright 浏览器内 fetch** → transcript-api 1.x → 可选 ASR |
| `collectors/adapters/youtube_transcript_browser.py` | **新增** Playwright 打开 watch 页，页面内 `fetch(captionUrl, credentials:'include')` + 监听 timedtext |
| `collectors/credentials.py` | **新增** `YouTubeCredentials` / `YOUTUBE_COOKIE` |
| `collectors/settings.py` | `YOUTUBE_BROWSER_TRANSCRIPT`、`YOUTUBE_ASR_FALLBACK` |
| `backend/platform_health.py` | `youtube_credentials` 健康检查 |

抓取顺序：

```
HTTP captionTracks → Playwright 浏览器字幕（PoToken 主路径）→ youtube-transcript-api → ASR（可选）
```

**PoToken 未 100% 消灭**：无 Playwright、无 Cookie、数据中心 IP 仍可能失败。建议本机 Chrome 复制 `youtube.com` Cookie 到 `YOUTUBE_COOKIE`。

### 4.5 脚本 / 配置

| 文件 | 说明 |
|------|------|
| `scripts/run_live_comparison.py` | 默认 **空 source_urls**，全靠 SKU 自动搜索；可选 `OPTIONAL_SOURCE_URLS` |
| `scripts/smoke_platforms.py` | `SMOKE_*` 仅连通性探测；B 站无 BV 则 skip |
| `.env.example` | 补充 smoke、trace、YouTube 相关变量 |

### 4.6 测试（新增）

- `tests/test_collection_trace_and_guards.py`
- `tests/test_youtube_transcript.py`
- `tests/test_youtube_browser_transcript.py`

---

## 5. 环境配置速查（`.env`）

```env
SPECS_FIRST_MODE=real

# 平台 Cookie（从 Chrome DevTools 复制，勿提交）
BILIBILI_SESSDATA / BILI_JCT / DEDEUSERID / BUVID3
TAOBAO_COOKIE
JD_COOKIE
REDDIT_COOKIE          # 可选，启用 Reddit 自动搜索
YOUTUBE_COOKIE         # 可选，提高 YouTube 浏览器字幕成功率

# YouTube
YOUTUBE_BROWSER_TRANSCRIPT=true
YOUTUBE_ASR_FALLBACK=false

# 日志
COLLECTION_TRACE=true
COLLECTION_TRACE_DIR=./vault_output

# Smoke 仅用于 scripts/smoke_platforms.py，非正式对比必填
SMOKE_BILIBILI_BVID=    # 留空 skip；勿用 BV1GJ411x7h7
```

---

## 6. 常用命令

```powershell
# 配置健康
python scripts/smoke_platforms.py --health-only

# 平台连通性（live）
python scripts/smoke_platforms.py

# 本地 Real 对比（自动搜索，无需手填 URL）
python scripts/run_live_comparison.py

# 快测
python -m unittest discover -s tests -p "test_youtube*.py" -q
python -m unittest discover -s tests -p "test_adapters.py" -q
```

---

## 7. 已知问题 / 限制

| 问题 | 现状 | 建议 |
|------|------|------|
| 淘宝滑块验证码 | 嵌入式 Streamlit 浏览器只能点不能拖；应用**弹出 Chrome/Edge** 手滑 | 更新 `TAOBAO_COOKIE`；商品直链减少搜索 |
| YouTube PoToken / IP 封禁 | 浏览器路径已加，非保证 | `YOUTUBE_COOKIE` + 本机 Playwright + 家庭 IP |
| B 站评测 | 自动 `site:bilibili.com` 搜索；需 Cookie 拉字幕/评论 | Source URLs 可补一条已知评测 |
| Reddit | Cookie HTTP 可读；pipeline 可能误 escalated 到 browser | 后续：HTTP 足够时不升浏览器 |
| 全量 unittest | `test_real_collector` / `test_api` 可能挂起 | 分批跑，勿后台全量 discover |
| `test_platform_health` 一条失败 | `gemini-3.5-flash` vs 测试期望 `2.5-flash` | 更新测试或 RECOMMENDED 常量 |

---

## 8. 用户侧已观察现象（会话记录）

- Smoke/health：Gemini、OpenAI、Bilibili、Taobao、JD、Reddit 配置曾全部 ok
- JD 价曾误报 ¥116 → mgets + HTML 逻辑已改（未 push）
- Real pipeline 曾在 Phase 1 `PAUSED_NEED_AUTH`（淘宝 captcha）
- B 站 `BV1GJ411x7h7` 为旧 smoke 占位，已守卫 + 文档说明

---

## 9. 建议下一步（接手人）

### P0 — 合并与验证

1. **Review + commit + push** 本机未提交改动（见 §4）
2. 本机跑 `smoke_platforms.py` + 快测套件
3. 用真实 SKU 跑 `run_live_comparison.py`，检查 `vault_output/collection_trace_*.log`

### P1 — 产品体验

1. Real 对比默认不依赖手填 URL（已完成脚本侧；UI 文案可再强调 optional）
2. 淘宝 captcha：引导用户在弹出窗口完成，成功后刷新 Cookie

### P2 — 平台深化

1. YouTube：实机验证 Playwright 字幕 + `YOUTUBE_COOKIE`；失败再走 ASR
2. Reddit：HTTP+Cookie 成功时跳过 browser escalation；改善 evidence 提取
3. B 站：搜索词/结果排序优化（优先「评测/缺点」）

### P3 — 工程

1. 给 `test_real_collector` / 全量 discover 加超时或标记 `@slow`，避免 CI/后台挂死
2. 修复 `test_platform_health` gemini 模型名断言

---

## 10. Git 状态快照

```
分支：main（推测）
未提交：约 14 个修改文件 + 6 个新文件（见 git status）
已推送：39afbba
```

提交前检查：勿提交 `.env`、Cookie、`vault_output/`、`__pycache__/`。

---

## 11. 关键文件索引

| 路径 | 用途 |
|------|------|
| `backend/pipeline.py` | 主流程状态机 |
| `collectors/real.py` | Real 采集编排 |
| `schemas/category_profile.py` | 搜索词模板 `video_search_queries` / `ecommerce_search_queries` |
| `collectors/browser.py` | Playwright 截图、captcha、in-page fetch |
| `collectors/adapters/youtube_transcript_browser.py` | YouTube PoToken 浏览器字幕 |
| `frontend/app.py` | Streamlit UI |
| `scripts/smoke_platforms.py` | 平台 smoke |
| `scripts/run_live_comparison.py` | 一键 Real 对比 |

---

*本文档由 Agent 根据当前工作区与对话上下文生成；push 后请以 git log 为准更新「远程最新提交」一节。*
