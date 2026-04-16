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
这会在数据库中写入提示，以便翻译守护进程提前启动 GPU 实例，将冷启动延迟从
约 60 秒降低到接近零。

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

- 为当前章节创建 `streaming_segments` 行（优先级 0 = 当前播放）
- 为下一章节创建行（优先级 1 = 预取）
- 每行代表一个 30 秒分段：
  `(audiobook_id, chapter_index, segment_index, locale, state='pending')`

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

当前章节（优先级 0）逐段处理以实现低延迟流式传输。预取章节（优先级 1）可作为
单个批量单元处理以提高效率。

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
| 跳转超出缓存范围 | `POST /api/translate/seek` → 从新位置重新排优先级 → 重新进入缓冲 |
| 跳转到批量缓存的章节 | 即时 — 已在永久缓存中 |

跳转端点将所有待处理分段降级（优先级 2），并将从跳转目标开始的 6 个分段提升
为优先级 0。

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
│  POST /api/translate/chapter-complete   工作节点回调（预取）       │
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
│  运行环境：Vast.ai L40S、RunPod 实例、或自托管 GPU                │
└───────────────────────────────────────────────────────────────────┘
```

## 设计参数

| 参数 | 值 | 原因 |
|------|---|------|
| 分段时长 | 30 秒 | L40S 处理约 2-3 秒；足够小以实现低延迟 |
| 缓冲阈值 | 6 个分段（3 分钟） | 足够的缓冲使播放不间断，同时 GPU 保持领先 |
| 预取 | 下一章节 | 无缝章节过渡，无需重新缓冲 |
| 活跃优先级 | 0 | 最先处理 — 用户正在收听的内容 |
| 预取优先级 | 1 | 在活跃分段完成后处理 |
| 降级优先级 | 2 | 跳转光标后方的分段 |

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

批量管道独立运行且可控：

**自动模式**（默认）：`audiobook-translate-check.timer` 每 5 分钟触发一次。
它查询 `translation_queue` 中的待处理行。如果存在待处理项且守护进程未运行，
则启动 `audiobook-translate.service`，该服务配置 GPU 实例、处理队列、
关闭 GPU，然后退出。

**手动模式**：禁用定时器可停止自动处理：

```bash
# 停止自动批量翻译
sudo systemctl stop audiobook-translate-check.timer

# 随时启动批量运行
sudo systemctl start audiobook-translate.service

# 重新启用自动模式
sudo systemctl start audiobook-translate-check.timer
```

守护进程管理完整的 GPU 生命周期 — 启动时配置实例，队列清空时关闭。
您只需为消耗的 GPU 小时付费。

**卡死检测**：如果工作节点停止进度超过 60 分钟（例如 SSH 隧道崩溃），
`translation-check.sh` 检测到陈旧的心跳，重启守护进程，并将卡住的行重置为
`pending`。

## 文件列表

| 文件 | 用途 |
|------|------|
| `library/backend/api_modular/streaming_translate.py` | 协调器 API（7 个端点） |
| `library/web-v2/js/streaming-translate.js` | 前端状态机 |
| `library/web-v2/css/shell.css` | 缓冲覆盖层样式 |
| `library/web-v2/shell.html` | 覆盖层标记 |
| `scripts/stream-translate-worker.py` | GPU 工作节点（分段处理） |
| `scripts/translation-daemon.sh` | 批量守护进程（GPU 生命周期） |
| `scripts/batch-translate.py` | 批量工作节点（章节处理） |
| `scripts/translation-check.sh` | 定时器驱动的批量启动器 |
| `systemd/audiobook-translate.service` | 批量守护进程服务单元 |
| `systemd/audiobook-translate-check.timer` | 5 分钟批量检查定时器 |
| `library/localization/pipeline.py` | 共享 STT → 翻译 → VTT 管道 |
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
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id    INTEGER NOT NULL,
    chapter_index   INTEGER NOT NULL,
    segment_index   INTEGER NOT NULL,
    locale          TEXT NOT NULL,
    state           TEXT DEFAULT 'pending',      -- pending, processing, completed, failed
    priority        INTEGER DEFAULT 1,           -- 0=活跃, 1=预取, 2=降级
    worker_id       TEXT,
    vtt_content     TEXT,                         -- 已完成分段的内联 VTT
    audio_path      TEXT,
    started_at      DATETIME,
    completed_at    DATETIME,
    UNIQUE(audiobook_id, chapter_index, segment_index, locale)
);
```

## 安全性

所有路由处理程序在边界处验证输入：

- **语言区域**：`_sanitize_locale()` 强制执行 `^[a-zA-Z]{2}(?:-[a-zA-Z0-9]{1,8})?$` —
  拒绝路径遍历（`../`）和日志注入（换行符、控制字符）
- **整数 ID**：`audiobook_id`、`chapter_index`、`segment_index` 在任何数据库查询
  或文件系统操作之前强制转换为 `int`
- **工作节点回调**（`segment-complete`、`chapter-complete`）：仅供 GPU 工作节点
  调用的内部端点，不暴露给浏览器客户端
