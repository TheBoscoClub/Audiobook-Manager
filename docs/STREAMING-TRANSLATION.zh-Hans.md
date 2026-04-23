# 流式翻译管道

按需实时翻译，由播放触发。当用户按下播放键收听未翻译的有声书时，系统会将
章节级任务分发给 GPU 工作节点，缓冲三分钟的翻译音频，然后开始播放。已预翻译
的书籍从缓存中即时加载。

## 为什么需要流式翻译

音频库包含 1,861 本有声书。预先批量翻译所有书籍（每个章节的 STT + DeepL +
TTS，每种语言）将花费数百美元的 GPU 费用。截至 v8.3.0，批量管道已预翻译
327 本书（5,245 个章节）。其余 1,534 本书尚未翻译。

流式翻译的解决方案是：只为用户实际收听的内容付费。

## 两条管道，一个缓存

| 管道 | 触发方式 | 处理方式 | 输出 |
|------|---------|---------|------|
| **批量** (`batch-translate.py`) | 定时器 + 队列 | 整章处理，后台运行 | 永久 VTT + TTS 音频 |
| **流式** (`streaming_translate.py`) | 播放触发 | 30 秒分段，实时处理 | 分段 → 合并 VTT |

两条管道都写入同一个永久缓存（`chapter_subtitles` 和 `chapter_translations_audio`
表）。一旦某个章节被任一管道翻译，以后的播放都是免费的。系统自我修复：用户的
收听习惯会逐渐填充缓存，批量管道在空闲时处理剩余内容。

## 端到端播放流程

### 第一阶段 — 打开应用（GPU 预热）

当应用打开且用户语言不是英语时，前端发送 `POST /api/translate/warmup` 请求。
这会在数据库中写入提示，流式工作节点据此向 STREAMING serverless 端点池
（RunPod 和/或 Vast.ai serverless，两者为对等供应商）发送一个预热请求。
STREAMING 端点的 `min_workers>=1`，常驻一个工作节点；此次预热用于验证
连通性并进一步降低首段延迟。完整的双供应商 D+C 拓扑、预热过期（15 分钟）
与卡住分段回收（10 分钟）机制请参见 `docs/SERVERLESS-OPS.md`。

### 第二阶段 — 按下播放

`shell.js` 调用 `streamingTranslate.check(bookId, locale)`，向协调器发送
`POST /api/translate/stream`：

```text
播放器 → 协调器 API → 数据库查询：
  ├── chapter_subtitles 存在？（批量缓存）
  ├── chapter_translations_audio 存在？（批量 TTS 缓存）
  │
  ├── 都存在 → { state: "cached" } → 即时播放
  │
  └── 缺失 → { state: "buffering", session_id, segment_bitmap }
```

### 第三阶段 — 缓冲状态

前端状态机从 `IDLE` 转换到 `BUFFERING`：

1. **视觉覆盖层**从播放器栏上方滑出 — 金色主题进度条显示分段完成情况
   （例如"3 / 6"）
2. **本地化音频通知**通过预生成的 edge-tts 语音播放（中文：*"请稍候，
   正在为您翻译本书。字幕和语音朗读即将开始。"*）
3. **主音频暂停** — 等待期间无需播放英文旁白

协调器同时执行：

- 为**光标缓冲填充**创建 `streaming_segments` 行 —— 即光标前方最初的 6 个 30 秒
  分段（约 3 分钟），以 **P0** 优先级入队
- 为当前章节剩余部分创建行，以 **P1** 优先级（向前追赶，直到章末或下一个
  合理断点）入队
- 每行代表一个 30 秒分段：
  `(audiobook_id, chapter_index, segment_index, locale, state='pending')`

完整的三级语义请参见下文的"优先级模型（以光标为中心）"章节。

### 第四阶段 — GPU 工作节点处理

`stream-translate-worker.py` 按优先级顺序轮询 `streaming_segments` 表并处理
每个分段：

```text
1. 原子性地获取下一个待处理分段（按优先级、章节、分段排序）
2. ffmpeg 流复制 → 从章节中提取 30 秒音频片段
3. STT（GPU 上的 faster-whisper）→ 原始英文转录
4. 翻译（DeepL API）→ 翻译文本
5. 生成带时间戳的 VTT
6. 根据分段在章节中的位置偏移时间戳
7. POST /api/translate/segment-complete → 报告内联 VTT 内容
```

P0 光标缓冲分段优先处理，以便播放尽快恢复。满足 3 分钟缓冲后，工作节点继续
处理 P1（向前追赶）以保持领先于光标，最后才处理 P2（回填），把光标后方的
时间线补齐，保证侧边字幕面板和将来向后拖动时的连贯性。

### 第五阶段 — 实时推送

当协调器收到分段完成回调时：

1. 在数据库中将分段状态更新为 `completed`
2. 通过 WebSocket 向所有连接的客户端广播 `segment_ready`
3. 广播 `buffer_progress`，包含已完成/总数计数

前端接收这些事件并实时更新进度条。

### 第六阶段 — 达到缓冲阈值

当 6 个分段完成（3 分钟音频）时，状态机从 `BUFFERING` 转换到 `STREAMING`：

- 覆盖层隐藏
- 通知音频停止
- 主音频**恢复** — 翻译字幕可用
- GPU 工作节点继续处理播放光标前方的剩余分段

### 第七阶段 — 跳转处理

| 操作 | 行为 |
|------|------|
| 缓冲范围内 ±30 秒 | 即时 — 分段已缓存，无中断 |
| 跳转超出缓存范围 | `POST /api/translate/seek` → 根据新光标重新排优先级 → 重新进入缓冲 |
| 跳转到批量缓存的章节 | 即时 — 已在永久缓存中 |

**跳转超出缓冲范围时**：所有现有的待处理分段会被降级为 **P2**；新光标前方的
6 个分段被提升或新插入为 **P0**（光标缓冲填充）；缓冲之后直至章末的剩余
部分排入 **P1**（向前追赶）；上一段已翻译尾部与新光标之间的空缺则排入
**P2**（回填），以保证侧边字幕面板和将来任何向后拖动操作的连贯性。

**停止播放时**：所有待处理分段会被降级为 **P2**。回填策略会保留已排入的工作
以便将来继续播放和补全侧边面板，而不是直接丢弃队列。

### 第八阶段 — 合并

当某章节的所有分段完成后，`_consolidate_chapter()` 执行：

1. 从所有分段行读取 VTT 内容
2. 去除重复的 `WEBVTT` 头部，合并为单个文件
3. 写入 `subtitles/{audiobook_id}/ch{N}.{locale}.vtt`
4. 插入 `chapter_subtitles` — 与批量管道使用的同一永久缓存

合并后，该章节与批量翻译的章节无法区分。

## 架构图

```text
┌───────────────────────────────────────────────────────────────────┐
│                        网页播放器                                  │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │ shell.js │──►│ streaming-   │──►│ 缓冲覆盖层               │  │
│  │ playBook │   │ translate.js │   │ （进度条 + 音频）         │  │
│  │ + 跳转   │   │ 状态机       │   └──────────────────────────┘  │
│  └──────────┘   └──────┬───────┘                                  │
│                         │                                          │
│            ┌────────────┼────────────┐                             │
│            │ WebSocket  │  REST API  │                             │
│            │ 事件推送   │  请求      │                             │
└────────────┼────────────┼────────────┼─────────────────────────────┘
             │            │            │
             ▼            ▼            ▼
┌───────────────────────────────────────────────────────────────────┐
│                     协调器 API                                     │
│                                                                    │
│  POST /api/translate/stream         请求流式翻译                   │
│  POST /api/translate/seek           处理跳转到未缓存位置           │
│  POST /api/translate/warmup         应用打开时预热 GPU             │
│  GET  /api/translate/segments/…     分段完成位图                   │
│  GET  /api/translate/session/…      会话状态                       │
│  POST /api/translate/segment-complete   工作节点回调               │
│  POST /api/translate/chapter-complete   工作节点回调（整章）       │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │ WebSocket 管理器：向所有客户端广播 segment_ready、       │     │
│  │   chapter_ready、buffer_progress 事件                    │     │
│  └──────────────────────────────────────────────────────────┘     │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│                        数据库                                      │
│                                                                    │
│  streaming_sessions     活跃会话跟踪、GPU 预热信号                 │
│  streaming_segments     每分段状态（pending/processing/            │
│                         completed/failed）、优先级、内联 VTT       │
│  chapter_subtitles      永久缓存（与批量管道共享）                 │
│  chapter_translations_audio  永久 TTS 缓存（共享）                │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│                     GPU 工作节点集群                                │
│                                                                    │
│  stream-translate-worker.py                                        │
│  ┌─────────────────────────────────────────────────────────┐      │
│  │ 轮询 streaming_segments（按优先级排序）                  │      │
│  │  → ffmpeg：提取 30 秒音频分段                           │      │
│  │  → faster-whisper：GPU 上的语音转文字                   │      │
│  │  → DeepL API：翻译转录文本                              │      │
│  │  → 生成带偏移时间戳的 VTT                               │      │
│  │  → POST /api/translate/segment-complete                 │      │
│  └─────────────────────────────────────────────────────────┘      │
│                                                                    │
│  调度目标：RunPod 和/或 Vast.ai serverless STREAMING 端点          │
│  （双供应商对等）—— 或自托管的 whisper-gpu 服务                    │
└───────────────────────────────────────────────────────────────────┘
```

## 设计参数

| 参数 | 值 | 原因 |
|------|---|------|
| 分段时长 | 30 秒 | L40S 处理约 2-3 秒；足够小以实现低延迟 |
| 缓冲阈值 | 6 个分段（3 分钟） | 足够的缓冲使播放不间断，同时 GPU 保持领先 |
| P0 —— 光标缓冲填充 | 光标前方 6 个分段 | 播放恢复前必须就绪 |
| P1 —— 向前追赶 | 从光标缓冲到章末 / 下一合理断点 | 播放过程中保持 GPU 领先于光标 |
| P2 —— 回填 | 从上一段已翻译尾部到光标 | 侧边字幕面板和向后拖动的连续性保障 |

## 优先级模型（以光标为中心，自 v8.3.8 起为四层）

调度器**以光标为中心**，而不是以章节为中心。分段根据与用户当前播放光标的
关系被排入四个优先级之一。v8.3.8 新增 **p2 = 试听（sampler）** 独立层级，
将原先的回填工作下移至 p3，从而确保 6 分钟预翻译试听永远不会抢占实时播放：

```text
优先级（数值越小越紧急）：
  0  P0 —— 光标缓冲填充。填充光标前方约 3 分钟（6 个分段），
         播放恢复前必须先到位。**仅限当前正在播放的书籍。**
  1  P1 —— 向前追赶。在光标缓冲之后继续产出分段，直到章末 /
         下一合理断点。仅当用户跳转或停止时才会被降级。
         **仅限当前正在播放的书籍。**
  2  P2 —— 试听（自 v8.3.8 起）。每本书开头的 6 分钟预翻译，
         适用于每个启用的非英语语言，成本有限。详见
         `docs/SAMPLER.md`。**数据库触发器强制保障**：任何
         尝试以 priority<2 插入/更新 origin='sampler' 行的操作
         都会被引擎 ABORT。
  3  P3 —— 回填与其他批量工作。产出上一段已翻译尾部与光标之间
         的分段；必须在以上优先级都满足后才运行，以保证侧边字幕
         面板和将来向后拖动时的上下文连贯。

根据触发器与不变量：当前正在播放书籍的实时播放（p0/p1）永远优先
于任何其他书籍的试听工作。试听绝不会从正在收听的用户手中夺走
GPU 的处理槽位。

跳转超出缓冲范围时：现有待处理的实时分段降级为 P3；新光标前方
6 个分段被提升或新插入为 P0；章末剩余部分排入 P1；上一段尾部
与新光标之间的空缺排入 P3。

停止播放时：所有待处理的实时分段降级为 P3（回填策略保留已排入
的工作以便将来继续播放和补全侧边面板）。
```

工作节点的领取顺序 —— `ORDER BY priority, chapter, segment` —— 保持不变；
v8.3.8 扩展了优先级层级，为试听提供了独立的受保护槽位。

### 试听与本模型的互动

试听在图书入库时持续以 p2 运行，但绝不会与实时播放（p0/p1）竞争。当用户
播放试听并越过**自适应缓冲触发阈值**（若所有已配置的 STT 后端均无
就绪工作者则为第 3 段；若任一后端温启则为第 4 段 —— 详见 `docs/SAMPLER.md`）时，前端会调用
`POST /api/translate/sampler/activate`，从光标向前创建 p0/p1 分段。GPU
冷启动的过程发生在用户仍在收听已缓存试听音频的时段内；等到 6 分钟试听
结束时，实时缓冲区已经开始填充。过渡无缝，用户几乎不会看到缓冲圈。

### 状态转换总览

| 事件 | P0（光标缓冲） | P1（向前追赶） | P2（回填） |
|------|-----------------|------------------|-------------|
| 按下播放 | 光标前方 6 个分段 | 当前章节剩余部分 | （空） |
| 跳转超出缓冲范围 | **新**光标前方 6 个分段 | 缓冲之后的剩余 | 所有原待处理分段 + 上一段尾部到光标的空缺 |
| 停止 | （空） | （空） | 所有待处理分段 |
| 恢复播放 | 光标前方 6 个分段（从 P2 重新提升） | 章节剩余部分（重新提升） | 上一段尾部到光标的剩余 |

## 状态机

```text
                    ┌──────────────────────────────┐
                    │                              │
                    ▼                              │
    ┌────────┐   check()   ┌────────────┐   达到阈值  ┌────────────┐
    │  IDLE  │────────────►│ BUFFERING  │──────────►│ STREAMING  │
    │ 空闲   │ （未缓存）  │ 缓冲中     │  （6段）   │ 流式播放   │
    └────────┘             │ • 覆盖层   │           │ • 播放中   │
        ▲                  │ • 音频通知 │           │ • 字幕开启 │
        │                  │ • 已暂停   │           │            │
        │                  └─────┬──────┘           └─────┬──────┘
        │                        │                         │
        │                   跳转超出                   跳转超出
        │                   缓存范围                   缓存范围
        │                        │                         │
        │                        ▼                         │
        │                  ┌────────────┐                  │
        │                  │ BUFFERING  │◄─────────────────┘
        │  全部缓存         │（跳转触发）│
        │  或英语           └────────────┘
        │                        │
        └────────────────────────┘
```

## 控制批量翻译

批量管道独立运行，调度目标是 BACKLOG serverless 端点池（冷池，
`min_workers=0`）。调度由 API 内部触发，也可通过 `scripts/batch-translate.py`
手动触发 —— 该脚本读取 `translation_queue` 并按章节逐一处理待处理行。

```bash
# 手动执行一次批量处理
sudo -u audiobooks /opt/audiobooks/library/venv/bin/python \
    /opt/audiobooks/scripts/batch-translate.py
```

无需管理 GPU 生命周期 —— serverless 端点会自动缩容至零，因此只需为实际
翻译的章节付费。BACKLOG 端点池在空闲时的成本为 0 美元。

**卡死检测**：`streaming_segments` 表中 `processing` 状态超过 10 分钟的
分段，会在流式工作节点的下一次轮询中被回收重试。批量侧的卡死行由 API
的 reconcile 循环重置为 `pending`。

## 文件列表

| 文件 | 用途 |
|------|------|
| `library/backend/api_modular/streaming_translate.py` | 协调器 API（7 个端点） |
| `library/web-v2/js/streaming-translate.js` | 前端状态机 |
| `library/web-v2/css/shell.css` | 缓冲覆盖层样式 |
| `library/web-v2/shell.html` | 覆盖层标记 |
| `scripts/stream-translate-worker.py` | 流式 GPU 工作节点（分段处理，对接 STREAMING 端点池） |
| `scripts/stream-translate-daemon.sh` | 流式工作节点的长驻外壳脚本 |
| `scripts/batch-translate.py` | 批量工作节点（章节处理，对接 BACKLOG 端点池） |
| `systemd/audiobook-stream-translate.service` | 流式工作节点服务单元 |
| `library/localization/pipeline.py` | 共享 STT → 翻译 → VTT 管道（`_remote_stt_candidates` 按 STREAMING 或 BACKLOG 调度） |
| `library/web-v2/audio/translation-buffering-*.mp3` | 本地化通知音频 |

## 数据库架构（迁移 004）

```sql
CREATE TABLE streaming_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id    INTEGER NOT NULL,
    locale          TEXT NOT NULL,
    state           TEXT DEFAULT 'buffering',    -- buffering, streaming, completed, warmup
    active_chapter  INTEGER DEFAULT 0,
    buffer_threshold INTEGER DEFAULT 6,
    gpu_warm        INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE streaming_segments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id        INTEGER NOT NULL,
    chapter_index       INTEGER NOT NULL,
    segment_index       INTEGER NOT NULL,
    locale              TEXT NOT NULL,
    state               TEXT DEFAULT 'pending',  -- pending, processing, completed, failed
    priority            INTEGER DEFAULT 1,       -- 0=P0 光标缓冲, 1=P1 向前追赶, 2=P2 回填
    worker_id           TEXT,
    vtt_content         TEXT,                    -- 目标语言（已完成分段）的内联 VTT
    source_vtt_content  TEXT,                    -- 源语言（英文）VTT（v8.3.2+）
    audio_path          TEXT,                    -- 每个分段的 opus 路径，由工作节点写入（v8.3.2+）
    retry_count         INTEGER DEFAULT 0,       -- 瞬时失败重试计数（v8.3.2+）
    started_at          DATETIME,
    completed_at        DATETIME,
    UNIQUE(audiobook_id, chapter_index, segment_index, locale)
);
```

该表结构通过 8.3.2 的数据迁移脚本（`003_streaming_segments.sh`、
`006_streaming_source_vtt.sh`、`007_streaming_retry_count.sh`）逐步演进；
每个脚本均幂等（`PRAGMA table_info` 守卫）且按版本边界门控
（`MIN_VERSION`），跨版本升级仅填充缺失的列。

## 在途 VTT 拼接（v8.3.7+）

清单和字幕获取路由将 `chapter_subtitles`（已完成、落盘的 VTT 文件）与
`streaming_segments` 的实时索引合并，即便某章节的 VTT 尚未完成整合，
只要第一个分段完成落库，就会立即出现在字幕列表中。

- **`/api/audiobooks/<id>/subtitles`** 返回两者的并集：
  （a）`chapter_subtitles` 中的缓存行，（b）由 `streaming_segments`
  按 `(chapter_index, locale)` 去重构建的索引。`subtitles.js` 的轮询无需
  等到章节整合就能发现正在流式处理的字幕轨。
- **`/api/audiobooks/<id>/subtitle/<chapter>/<locale>`** 在磁盘无缓存文件
  （或 `chapter_subtitles` 有记录但文件缺失）时，直接从
  `streaming_segments` 构建拼接后的 VTT 返回。拼接过程剥离每个分段的
  `WEBVTT` 头部，按 `segment_index` 顺序输出单一的 `WEBVTT` + 连续 cues。
- 对于 `locale='en'`，拼接器读取 `source_vtt_content`（Whisper 转录与
  语言无关）；其他语言读取 `streaming_segments.locale` 匹配的 `vtt_content`。
- 拼接的 VTT **永不缓存到磁盘** —— 每次请求都从分段行重新构建，
  保证后到的分段在下一次拉取时即可显现。
- 错误辨别得到保留：`chapter_subtitles` 中存在行但磁盘文件缺失仍返回
  `VTT file missing on disk` (404)；完全没有记录则返回
  `Subtitle not found` (404)。

## 遗留队列的延迟状态（v8.3.7+）

`library/localization/queue.py::get_book_translation_status` 将非英文
locale 的 `pending` / `processing` / `failed` 行折叠为
`{"state": "deferred", "reason": "streaming_pipeline"}`，避免将
流式管道上线前遗留的批处理失败暴露给 UI。在此之前，首次打开任一
尚未翻译的 zh-Hans 书都会显示来自 `translation_queue` 的过期
`字幕生成失败 — No STT provider configured` 提示 —— 这些记录早在
旧批处理 worker 停止运转数月前就已经是失败状态。非英文 locale
现在的规范进度展示面板是流式 overlay
（`library/web-v2/js/streaming-overlay.js`）；已完成的遗留记录
（合法的磁盘 VTT 情况）仍按原样通过。`'en'` locale 豁免 —— 英文
的 STT 失败是真实情况，不是过期数据。

## 安全性

所有路由处理程序在边界处验证输入：

- **语言区域**：`_sanitize_locale()` 强制执行 `^[a-zA-Z]{2}(?:-[a-zA-Z0-9]{1,8})?$` —
  拒绝路径遍历（`../`）和日志注入（换行符、控制字符）
- **整数 ID**：`audiobook_id`、`chapter_index`、`segment_index` 在任何数据库查询
  或文件系统操作之前强制转换为 `int`
- **工作节点回调**（`segment-complete`、`chapter-complete`）：仅供 GPU 工作节点
  调用的内部端点，不暴露给浏览器客户端
