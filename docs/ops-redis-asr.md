# 基础设施现状：Redis 与本地 ASR

> 更新：2026-07-17

本文梳理 Specs-First 当前 **Redis checkpoint** 与 **本地音频转写（ASR）** 的配置、验证方式与后续步骤。

---

## 1. Redis

### 用途（很窄）

Redis **仅**用于 HITL 任务 checkpoint（验证码挂起 → 续传），**不**承担：

- EventBus / SSE 推送（进程内 `InMemoryEventBus`）
- 采集缓存、限流、会话共享

实现见 `backend/checkpoint.py`：`REDIS_URL` 非空且 `ping` 成功 → `RedisCheckpointStore`；否则 → `MemoryCheckpointStore`。

### 当前建议

| 场景 | 是否值得上 Redis |
|------|------------------|
| 本机 Streamlit 单机跑 Mock/Real | **否** — 保持 `REDIS_URL` 为空 |
| 同进程内验证码续传 | **否** — 内存 checkpoint 足够 |
| 多 uvicorn worker / API 与 UI 分进程 | **是** — 需共享 checkpoint |
| 进程重启后仍要续传挂起任务 | **是** |

**结论（2026-07）**：本地开发 **不必** 安装 Redis。上云或多实例时再配：

```env
REDIS_URL=redis://localhost:6379/0
```

Key 前缀：`specs-first:checkpoint:{task_id}`

---

## 2. 本地 ASR（音频转写）

### 能力边界

| 入口 | 说明 |
|------|------|
| Streamlit 侧边栏「高级选项」→ **本地 ASR 转写** | 手动填视频 URL 转写 |
| `GET /asr/status` · `POST /asr/transcribe` | API 手动触发 |
| B 站采集 `BILIBILI_ASR_FALLBACK=true` | 无 CC 字幕时自动兜底 |
| YouTube `YOUTUBE_ASR_FALLBACK=false`（默认关） | 字幕全失败时的最后手段 |

核心模块：`collectors/asr.py`

### 可选依赖

```powershell
pip install -e ".[asr]"      # yt-dlp + faster-whisper（多语言）
pip install -e ".[asr-zh]"   # 上面 + funasr / SenseVoice（中文推荐，含 torch）
```

| 组件 | 作用 |
|------|------|
| **yt-dlp** | 下载音轨（CLI 或 Python 模块，优先 m4a/webm，无需 ffmpeg 转码） |
| **SenseVoice**（funasr + torch） | 中文/混合内容，CPU 友好，采集兜底默认语言 `zh` |
| **faster-whisper** | 多语言兜底，SenseVoice 不可用时自动选用 |

### 健康检查

- `GET /health` → 检查项 `asr_stack`
  - `ok`：后端 + yt-dlp 均就绪
  - `warn`：开启了 B 站/YouTube ASR 兜底但依赖缺失
  - `skip`：未开兜底且 ASR 为可选能力

### 一键自检

```powershell
python -c "from collectors.asr import check_readiness; import json; print(json.dumps(check_readiness().to_dict(), indent=2, ensure_ascii=False))"
```

期望输出（就绪时）：

```json
{
  "ready": true,
  "backend": "sensevoice",
  "yt_dlp": "cli",
  "missing": [],
  "pipeline_fallback_enabled": true
}
```

### 手动转写 smoke

```powershell
uvicorn backend.api:app --reload
# 另开终端
curl http://127.0.0.1:8000/asr/status
# POST /asr/transcribe  body: {"url":"https://www.bilibili.com/video/BV...", "language":"zh"}
```

或在 Streamlit「高级选项」填 URL → **本地转写**。

转写缓存目录：`vault_output/asr_cache/`

---

## 3. 本机验证记录（2026-07-17）

| 检查项 | 结果 |
|--------|------|
| 单元测试 | `python -m unittest discover -s tests` → **211 passed** |
| `REDIS_URL` | 未配置 → 内存 checkpoint |
| ASR `ready` | **true** |
| 实际后端 | **faster-whisper**（yt-dlp CLI 可用） |
| SenseVoice | funasr 已装，但 **torch 未装** → 自动降级 faster-whisper |
| B 站 ASR 兜底 | `BILIBILI_ASR_FALLBACK=true` |
| YouTube ASR 兜底 | `YOUTUBE_ASR_FALLBACK=false` |

---

## 4. 安装 asr-zh 后的下一步

### 路径 A：立刻可用（当前状态）

已具备 **faster-whisper + yt-dlp**，可直接：

1. 刷新 Streamlit Health → `asr_stack` 应为 **ok**
2. 侧边栏填一条 **B 站或 YouTube 短视频 URL** → 点「本地转写」
3. Real 模式跑含无字幕 B 站视频的 SKU，观察采集 trace 是否出现 `falling back to local ASR`

中文内容用 faster-whisper 可用，质量通常略逊于 SenseVoice。

### 路径 B：启用 SenseVoice（中文推荐）

若 `check_readiness()` 显示 `backend: faster-whisper` 且终端有 `No module named 'torch'`：

```powershell
pip install torch
# 或 CPU 版（体积较小）：
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

然后重新自检，应变为 `"backend": "sensevoice"`。  
**首次转写**会下载 SenseVoice 模型（数百 MB），属正常现象。

### 路径 C：Real 管线联调

1. 侧边栏配置 B 站 Cookie（CC 字幕 + 评论）
2. 选一条**无 CC 字幕**的 B 站评测 URL 加入 Source URLs
3. Real + Playwright 跑对比，在 `vault_output/collection_trace_*.log` 搜 `local ASR`

### 不建议现在做

- 为本机单机部署 Redis
- 打开 `YOUTUBE_ASR_FALLBACK`（除非浏览器字幕 + transcript-api 均失败且确需兜底）

---

## 5. 相关文件

| 文件 | 说明 |
|------|------|
| `collectors/asr.py` | 下载、转写、readiness |
| `backend/platform_health.py` | `check_asr_stack()` |
| `backend/checkpoint.py` | Redis / 内存 checkpoint |
| `.env.example` | `REDIS_URL`、`BILIBILI_ASR_FALLBACK`、`YOUTUBE_ASR_FALLBACK` |
| `pyproject.toml` | `[project.optional-dependencies]` → `asr` / `asr-zh` |
| `tests/test_asr.py` | ASR 单元测试 |
