这是一份融合了 **Gemini 1.5（大吞吐、高带宽、省算力）** 与 **OpenAI（强约束、不翻车、精仲裁）** 的双脑混合架构项目计划书。

系统全面砍掉了易卡死的同步等待逻辑，转为**人机协同异步事件流**，并完美打通 **Obsidian 原生资产下沉**。

---

# Specs-First (不服跑个分) 项目计划书 v4.0 (混合双脑版)

## 一、 项目定位与核心愿景

Specs-First 是一款专注于反消费主义营销、数据驱动的本地商品特征提取与渐进式比对系统。
通过前端（Streamlit）与后端（FastAPI）完全解耦的异步事件驱动架构，利用 **Gemini 吞噬海量文本**、**OpenAI 锁死输出格式**，将官方规格、视频字幕、极客长文及高墙论坛“无缝脱水”，建立纯粹由冰冷参数、实测优缺点和真实到手价构成的横向对比矩阵，最终无缝沉淀为 Obsidian 的 **Frontmatter 原生 Dataview 资产**。

---

## 二、 混合架构与双脑选型（Hybrid Brain Architecture）

为了在**海量文本吞吐**、**多模态长图 OCR** 以及**最终输出稳定性**之间取得完美平衡，系统采用混合模型策略：

```
                              ┌──► Phase 1/2/3 (海量文本 + 多模态 OCR)
                              │    └─► Gemini 1.5 Flash 
                              │        [百万上下文 + 多模态 OCR，吞噬弹幕/长文/电商截图]
                              │
[FastAPI 后端事件总线] ───────┤
                              │
                              └──► Phase 4 (结构化终审与落盘)
                                   └─► OpenAI gpt-4o / gpt-4o-mini
                                       [仅 Structured Outputs (strict:true)，锁死 JSON/YAML 格式]

```

### 核心技术栈与职责划分

| 架构层级 | 技术选型 | 核心职责与优化逻辑 |
| --- | --- | --- |
| **前端交互层** | **Streamlit + SSE 监听器** | 摒弃高频轮询。前端通过 SSE (Server-Sent Events) 接收后端状态流，AgGrid 仅做最终渲染。进度和日志流式展示，防止页面重刷（Rerun）导致组件丢失焦点。 |
| **后端调度层** | **FastAPI + Asyncio + Redis** | 常驻守护进程。引入 **HITL (人机协同) 挂起队列**。当爬虫遇到滑块验证码时，持久化当前 Session 并释放当前 Worker，绝不原地 await 死等，拒绝 OOM（内存溢出）。 |
| **读料/OCR 大脑** | **Gemini 1.5 Flash** | **专攻 Phase 1/2/3**：大上下文吞噬论坛长文、视频字幕、弹幕；**多模态 OCR** 读取电商截图到手价。 |
| **格式锁死大脑** | **OpenAI `gpt-4o` 家族** | **仅 Phase 4**：开启 **`Structured Outputs` (`strict: true`)**，锁死 JSON/YAML Schema；**不做 OCR/读长文**。 |

---

## 三、 四阶非阻塞状态机与双层 Schema 流程

系统采用**非阻塞式、可断点续传的状态机**，配合“通用硬指标 + 外挂属性桶”的双层 Schema，规避横向对比时的“稀疏矩阵（维度灾难）”。

```
[Phase 0: 意图消歧] ──► [Phase 1: 权威骨架] ──► [Phase 2: 民间脱水] ──► [Phase 3: Gemini OCR 到手价] ──► [Phase 4: OpenAI 结构化落盘]
 (模糊输入->勾选SKU)    (Gemini填官方槽)       (Gemini万字排毒)      (Gemini截图OCR)         (OpenAI Strict JSON)

```

### Phase 0: 意图消歧与基础建表 (Base JIT Schema)

* **动作**：用户在前端输入模糊词（如：“Zeiss 50mm”）。轻量探针扫描出前 10 个具体 SKU 供用户精准勾选。
* **建表（普适，无预设品类）**：勾选 SKU 后，对详情图做轻量探针 → **Gemini 识图**梳理参数线索 → **OpenAI Structured Outputs** 锁定该任务的品类标签、**5–8 个硬指标槽**、别名与对比/检索关键词。独有参数统一进 `spec_highlights`，避免列爆炸。无图时仅用 query/候选标题建表；无 API 时回退 `parameter_a…h`。

### Phase 1: 权威骨架与基准锁死 (Official Base)

* **动作**：后端定向检索品牌官网/白皮书，Playwright 抓取纯文本。由 **Gemini 1.5 Flash** 填槽，锁定不可篡改的 `official` 参数作为对比基准线。

### Phase 2: 多路民间脱水与按需降级 (Real-World Extraction)

* **动作**：并发拉取视频字幕、热评、图文及高墙论坛讨论（支持 URL 定点注入绕过搜索）。
* **排毒**：**Gemini 1.5 Flash** 开启“毒舌质检员” Prompt，利用上下文缓存低成本清洗海量噪声，过滤水军商业话术和主观情绪，只提炼实测翻车表现（如：“边缘色散严重”、“对焦环阻尼轻微不均”），塞入 `real_world` JSON 分支。
* **降级**：若无原生字幕，打上 `[待音视频硬解]` 标签，不自动调用本地 Whisper，改为用户手动触发。

### Phase 3: 视觉 OCR 与价格归一 (Gemini Multimodal OCR)

* **动作**：Playwright 截图电商平台百亿补贴详情页，进行 `2048px` 垂直切片，送入 **Gemini 1.5 Flash 多模态 OCR** 提取真实到手价。
* **HITL 介入**：遭遇滑块验证时，后端标记任务为 `PAUSED_NEED_AUTH`，抛出事件并休眠，前端渲染滑块窗口。用户过验后，后端载入 Session 续传。

### Phase 4: 冲突仲裁与结构化落盘 (OpenAI Structured Output)

* **冲突仲裁**：当民间实测与官方参数发生严重冲突时，由 **OpenAI** 在 **Structured Outputs (`strict: true`)** 下输出终审 JSON/YAML，锁死格式。

---

## 四、 本地第二大脑（Obsidian）原生资产沉淀

拒绝在单个 Markdown 中塞入臃肿易炸的二维大表。落地采用 **“一 SKU 一文本文件 + 头部 Frontmatter 元数据 + 主视图 Dataview 动态渲染”** 的现代化知识库管理方式。

### 1. 落地 Vault 目录结构

```
📁 Obsidian_Vault/
│  📁 00_Specs_First_Matrix/
│  │  📄 📷 镜头渐进式比对矩阵.md     <-- 主视图：用 Dataview 动态渲染大表
│  📁 01_Product_Items/
│  │  📄 📄 Zeiss_50mm_f2_MP.md       <-- 实体数据：由 OpenAI 强约束写入的纯文本
│  │  📄 📄 Sony_50mm_f12_GM.md

```

### 2. 实体数据文件示例 (`Zeiss_50mm_f2_MP.md`)

Phase 4 由 **OpenAI `gpt-4o-mini**` 开启 `Structured Outputs` 焊接落盘，YAML 格式绝对对齐。

```markdown
---
tags: [Specs-First, Product/Lens]
sku: "Zeiss Makro-Planar T* 50mm f/2"
brand: "Zeiss"
price_real_world: 4899
optical_structure_official: "6组5片"
spec_highlights:
  - "浮动镜片组"
  - "T* 幕"
critical_flaws:
  - "边缘色散较严重 (来源: Bilibili_Review_AV12345)"
  - "对焦环阻尼轻微不均 (来源: Chiphell_Thread_8876)"
arbitration_summary: "官方光学规格属实。但大光圈下边缘色散明显，且二手市场存在轻微个体对焦品控差异。"
---

# 🔎 Specs-First 脱水报告: Zeiss Makro-Planar T* 50mm f/2

## 🎯 核心仲裁结论
> [!WARNING]
> **冲突仲裁结果**：`{{arbitration_summary}}`

## 📊 原始信源溯源日志
* **官方白皮书**：[Official_Spec_PDF](https://...)
* **民间翻车点 1**：Bilibili 评测 - 边缘色散严重 [视频直达](https://...)
* **民间翻车点 2**：Chiphell 论坛 - 对焦阻尼不均 [帖子直达](https://...)

```

### 3. 主视图大表渲染 (`镜头渐进式比对矩阵.md`)

利用 Obsidian 事实上的数据核心 `Dataview` 插件。用户在本地打开此文件时，Obsidian 会**瞬间在本地渲染出跨文件的横向对比矩阵**，彻底脱离对 Specs-First 前端系统的依赖。

```
# 📷 镜头参数与真实翻车点横向比对矩阵
```
```dataview
TABLE 
    optical_structure_official AS "官方结构",
    spec_highlights AS "独有特性",
    price_real_world AS "真实到手价(元)",
    critical_flaws AS "💥 民间实测翻车点",
    arbitration_summary AS "⚖️ 终审仲裁"
FROM #Specs-First AND "01_Product_Items"
SORT price_real_world ASC

```

---

## 五、 项目下一阶段核心里程碑
1. **[Milestone 1] 混合模型路由搭建**：跑通 FastAPI 任务管道，用 Gemini 吞大文本，用 OpenAI 的 Strict JSON 写出第一批绝对不乱码的 SKU Markdown 文件。
2. **[Milestone 2] 人机协同断点续传**：攻克 Playwright 触发防爬时的 Session 序列化，实现后端 Worker 优雅休眠与前端验证码弹窗同步。

