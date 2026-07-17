# 基础设施现状：Redis 与本地 ASR

> 更新：2026-07-17（SenseVoice + ffmpeg 已本机验证）

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
pip install -e ".[asr-zh]"   # 上面 + funasr / SenseVoice（中文推荐，含 torch + torchaudio）

# SenseVoice 解码 m4a/webm 需要 ffmpeg（本机已验证 winget 安装）
winget install --id Gyan.FFmpeg -e
```

| 组件 | 作用 |
|------|------|
| **yt-dlp** | 下载音轨（CLI 或 Python 模块；自动写入平台 Cookie 到 Netscape jar） |
| **SenseVoice**（funasr + torch + torchaudio） | 中文/混合；缺 torchaudio 时自动降级 faster-whisper |
| **faster-whisper** | 多语言兜底；SenseVoice 解码失败时二次回退 |
| **ffmpeg** | 将 m4a/webm 转为 16k mono wav，并按 `ASR_MAX_AUDIO_SECONDS` 截断 |

### 健康检查

- `GET /health` → 检查项 `asr_stack`
  - `ok`：后端 + yt-dlp 均就绪
  - `warn`：开启了 B 站/YouTube ASR 兜底但依赖缺失
  - `skip`：未开兜底且 ASR 为可选能力
- `/asr/status` 额外返回 `ffmpeg: true/false`

### 一键自检

```powershell
# 仅 readiness
python scripts/smoke_asr.py

# 无网络：加载 SenseVoice 并对合成 wav 跑一次 forward
python scripts/smoke_asr.py --self-test

# 端到端（B 站通常比 YouTube 更易过 bot 校验）
python scripts/smoke_asr.py --url https://www.bilibili.com/video/BVxxxx --language zh

# 已有本地音频
python scripts/smoke_asr.py --file path\to\audio.wav --language zh
```

期望 readiness：

```json
{
  "ready": true,
  "backend": "sensevoice",
  "yt_dlp": "cli",
  "ffmpeg": true,
  "pipeline_fallback_enabled": true
}
```

转写缓存目录：`vault_output/asr_cache/`

### 配置项

| 变量 | 默认 | 说明 |
|------|------|------|
| `BILIBILI_ASR_FALLBACK` | `true` | 无 CC 时走本地 ASR |
| `YOUTUBE_ASR_FALLBACK` | `false` | YouTube 字幕全失败后的最后手段 |
| `ASR_MAX_AUDIO_SECONDS` | `600` | ffmpeg 截断上限，避免长评测卡死 CPU |
| `YOUTUBE_COOKIE` / B 站 Cookie | — | yt-dlp 下载时自动注入 |

---

## 3. 本机验证记录（2026-07-17）

| 检查项 | 结果 |
|--------|------|
| 单元测试 | 见本次提交后 `python -m unittest discover -s tests` |
| `REDIS_URL` | 未配置 → 内存 checkpoint |
| ASR `ready` | **true**，`backend=sensevoice` |
| torch / torchaudio | 已装（CPU 轮） |
| ffmpeg | **已装**（`winget install Gyan.FFmpeg`） |
| `--self-test` | SenseVoice 模型首次下载约 936MB 后 **ok** |
| B 站 m4a → wav → 转写 | **ok**（20s 片段） |
| YouTube yt-dlp | 仍可能被 bot 校验拦截；需从浏览器导出**完整** Cookie（仅 header 片段常不够） |
| B 站 ASR 兜底 | `BILIBILI_ASR_FALLBACK=true` |
| YouTube ASR 兜底 | `YOUTUBE_ASR_FALLBACK=false` |

---

## 4. 下一步（Real 联调）

1. Streamlit Health → 确认 `asr_stack` 为 ok
2. 侧边栏「本地 ASR 转写」填 **B 站短视频** 试跑
3. Real 模式：Source URLs 加一条**无 CC** 的 B 站评测，在 `vault_output/collection_trace_*.log` 搜 `local ASR`
4. YouTube 若需 ASR 下载：用浏览器扩展导出 Netscape cookies，或刷新 `YOUTUBE_COOKIE` 后再试

### 不建议现在做

- 为本机单机部署 Redis
- 默认打开 `YOUTUBE_ASR_FALLBACK`（浏览器字幕路径优先）

---

## 5. 相关文件

| 文件 | 说明 |
|------|------|
| `collectors/asr.py` | 下载、Cookie、ffmpeg 截断、转写、readiness |
| `scripts/smoke_asr.py` | readiness / self-test / URL / file smoke |
| `backend/platform_health.py` | `check_asr_stack()` |
| `backend/checkpoint.py` | Redis / 内存 checkpoint |
| `.env.example` | Redis、ASR、时长上限 |
| `pyproject.toml` | `asr` / `asr-zh` extras（含 torchaudio） |
| `tests/test_asr.py` | ASR 单元测试 |
