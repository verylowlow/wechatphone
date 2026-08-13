# wechatphone - 社交语音电话 AI 桥接 (微信 / 钉钉 / 企业微信)

> 📘 文档: [`docs/操作手册.md`](docs/操作手册.md)（启动/任务下达/批量/各应用 SOP）、
> [`docs/测试手册.md`](docs/测试手册.md)（自检/回归/测试矩阵）。
> 版本基线 2026-08-10：微信 4.1.12.26、企业微信 WXWork.exe 均已实测。

将 PC 端社交应用的语音通话桥接到阿里云 Qwen-Audio Realtime, 让 AI 代替你说话/听话。
只需 **一条 VB-Cable**, 通过"物理捕获 + 虚拟注入"两条独立路径实现回声隔离。
支持**本地知识库**, 让 AI 在通话中检索业务资料回答专业问题。

> **多应用**: 音频桥接核心工作在设备层, 与具体 App 无关——微信/钉钉/企业微信
> (乃至任何 WebRTC 电话) 原理上都能用。差异只在 UI 自动化层(拨号/接听/挂断按钮),
> 已抽象为 `adapters` 配置驱动。用 `--app` 指定端; `--manual` 纯音频模式则
> 任何 App 零适配可用。

## 链路 (单条 VB-Cable, 物理隔离回声)

```
微信对方声音 → 微信扬声器(物理扬声器) ──loopback捕获──> 本程序 ──> 阿里云 Realtime
阿里云 AI 语音 → 本程序 ──写入──> CABLE Input ═══> CABLE Output ──> 微信麦克风 ──> 对方
```

**回声隔离原理**: AI 的声音只写进 `CABLE Input`, 永远不上物理扬声器;
而捕获只 loopback 物理扬声器。两条路径物理分离, AI 听不到自己, 不会自激。
额外好处: 你自己能从物理扬声器听到对方说话。

## 前置条件

1. **VB-Cable 已安装** (已完成)。系统里有 `CABLE Input` (输出端) 和 `CABLE Output` (输入端) 两个设备。

2. Python 依赖已装在 `.venv`: `pyaudiowpatch`(支持 WASAPI loopback) / `numpy` / `websockets`。

## 运行

```bash
# 列出音频设备
.venv/Scripts/python.exe bridge.py --list

# 启动 (默认 --app wechat, 自动识别: 捕获=物理扬声器loopback, 注入=CABLE Input)
.venv/Scripts/python.exe bridge.py

# 指定应用端 (决定来电接听/挂断/默认麦切换等 UI 自动化行为)
.venv/Scripts/python.exe bridge.py --app dingtalk
.venv/Scripts/python.exe bridge.py --app wecom

# 纯音频桥模式: 不做任何 UI 自动化, 任何 App 零适配可用 (手动接听/挂断)
# 通话记录与知识库照常工作; 适合还没写适配器的新应用
.venv/Scripts/python.exe bridge.py --manual
.venv/Scripts/python.exe bridge.py --app dingtalk --manual

# 手动指定设备索引
.venv/Scripts/python.exe bridge.py --capture-idx 16 --inject-idx 13
```

> 单实例单通话: 一次 bridge 进程只服务一个应用端的一路通话, 切换应用需重启。

## 应用端设置 (关键)

**核心原则**: 音频桥接工作在设备层, 与应用无关。无论哪个 App, 你只需保证两点:
1. 该 App 的**扬声器/输出** = 本机物理扬声器 (程序从这里 loopback 捕获对方声音)
2. 该 App 的**麦克风/输入** = `CABLE Output` (AI 的声音从这里注入给对方)

各 App 的设置入口不同:

| 应用 | 麦克风/扬声器设置方式 |
|---|---|
| 微信 | 无持久设备设置, 跟随**系统默认麦克风**。bridge 启动时自动把系统默认麦切到 `CABLE Output`(pycaw), 退出还原; `--no-default-mic` 可关 |
| 钉钉 | 通话窗口/设置内**应用内**选麦克风=CABLE Output, 扬声器=物理扬声器 |
| 企业微信 | 通话音频设置内**应用内**选麦克风=CABLE Output, 扬声器=物理扬声器 |

然后正常拨打/接听语音电话即可。你自己能听到对方, 但麦克风已被 AI 接管。

> 注意: 系统默认输出设备**不要**设成 CABLE Input, 否则程序自动捕获会选错。
> 保持默认输出为物理扬声器即可。

## 自测 (不用真打电话)

1. **API 自检** (不依赖音频, 纯文本验证 API 是否回复):
   ```bash
   .venv/Scripts/python.exe test_api_text.py
   ```
2. **虚拟声卡穿透自检** (验证 CABLE Input→Output 数据流通):
   ```bash
   .venv/Scripts/python.exe test_inject.py   # 应打印 INJECT TEST: PASS
   ```
3. **全链路自测** (已验证通过):
   ```bash
   .venv/Scripts/python.exe bridge.py
   # 另开一个终端:
   ffplay -nodisp -autoexit tools/test_voice.wav   # 往物理扬声器播一段人声
   ```
   正常会看到: 电平升高 → speech_started → [对方说] 转写 → AI 回复文字 → response.done

## 配置 (.env)

| 变量 | 说明 |
|---|---|
| `ALIYUN_REALTIME_API_KEY` | 阿里云百炼 API Key |
| `ALIYUN_REALTIME_BASE_URL` | 工作空间 MaaS 地址 |
| `ALIYUN_REALTIME_MODEL` | 默认 `qwen-audio-3.0-realtime-plus` |
| `ALIYUN_REALTIME_VOICE` | 音色, 默认 `longanqian`, 可换复刻音色 |
| `ALIYUN_TURN_DETECTION` | `server_vad`(默认,参数可控) 或 `smart_turn`(语义轮次) |
| `ALIYUN_VAD_THRESHOLD` | server_vad 灵敏度, [-1,1], 默认 0.5 |
| `ALIYUN_SILENCE_DURATION_MS` | 判停静音时长, [200,6000], 默认 800 |
| `ALIYUN_NOISE_GATE` | AI说话期间噪声门阈值, 默认 500 |
| `KNOWLEDGE_ENABLED` | 知识库总开关, `1`(默认) / `0` |
| `KNOWLEDGE_BACKEND` | 后端, 目前仅 `local`(SQLite+numpy 嵌入式混合检索) |
| `KNOWLEDGE_EMBEDDING_API_URL` | 向量化 API (OpenAI 兼容), 默认 DashScope compatible-mode |
| `KNOWLEDGE_EMBEDDING_API_KEY` | 向量化 API Key (与 Realtime 同一个即可) |
| `KNOWLEDGE_EMBEDDING_MODEL` | 向量模型, 默认 `text-embedding-v4` |
| `CALLLOG_ENABLED` | 通话记录总开关, `1`(默认) / `0` |
| `OUTBOUND_OPEN_DELAY` | 外呼开场: 任务注入后多少秒无人出声让 AI 先开口, 默认 5 |
| `INCOMING_GREETING` | 来电开场白: AI 自动接听来电后第一句话 (固定文案, 默认"您好, 电话已经接通了, 请问有什么事吗?") |
| `OUTBOUND_DEFAULT_OPENING` | 外呼开场白默认值: 任务发起人未指定开场白时的回退 |
| `AUTO_ANSWER` | 来电自动接听, `1`(默认) / `0`; CLI `--no-auto-answer` 临时关闭 |
| `AUTO_ANSWER_VIDEO` | 视频来电是否也接, 默认 `0`(只接语音, 视频跳过) |
| `AUTO_ANSWER_POLL` | 来电弹窗轮询间隔秒, 默认 1.0 |

## 知识库模块 (模块1)

让 AI 在通话中检索你的业务资料(价目表/FAQ/政策), 回答专业问题而不瞎编。

### 架构
- **Adapter 模式**: `knowledge/base.py` 定义 `KnowledgeAdapter` 接口
  (`ingest_file` / `query` / `list_documents` / `delete_document` / `get_stats`),
  默认 `LocalKnowledgeAdapter`, 未来可插 RAGFlow / Chroma 后端。
- **混合检索**: `0.65×向量余弦 + 0.35×关键词(jieba+IDF)`, 移植自 newcallcall 实战经验。
- **三级注入** (newcallcall `kb_injection` 思想):
  语料 ≤6000 token → `FULL` 全文直接注入 instructions(无需工具);
  更大 → `RETRIEVAL` 注入置顶卡+文档索引, 模型调用 `search_knowledge` 工具按需检索。
- **置顶卡**: 在管理页把文档标为"置顶"后, 该文档**永远全文注入**, 是防报价幻觉的最后防线。
- **Embedding 降级**: API 未配置/失败时自动退回 256 维哈希向量, 保证系统永不崩。

### 使用
```bash
# 1. 启动知识库管理页 (上传/置顶/删除/检索测试)
.venv/Scripts/python.exe kb_app.py            # http://127.0.0.1:8765

# 2. 在浏览器上传 txt/md/html/csv/pdf/docx 文件

# 3. 正常启动桥接, 知识库自动加载进通话
.venv/Scripts/python.exe bridge.py
# 日志会显示: [KB] 知识库已加载 ... / [KB] 注入策略: tier=FULL|RETRIEVAL
# AI 调用工具时: [KB] 工具调用: search_knowledge({...}) / [KB] 已回传工具结果
```

### 自检
```bash
.venv/Scripts/python.exe tools/test_kb.py   # 应打印 PASS: knowledge smoke test
```

## 通话记录模块 (模块3)

自动把每通桥接电话记录进 SQLite (`data/calls.sqlite`), 并提供 Web 查看。

- **通话边界**: 基于活动检测——首个远端语音/转写事件开启通话, 90 秒无活动自动收尾,
  bridge 退出时强制收尾 (无需应用 API)。
- **记录内容**: 对方说的话(转写)、AI 说的话(流式累积)、知识库工具调用(名称+参数+命中数)、环境音。
- **应用与联系人**: 每通记录自动标注 `app` (wechat/dingtalk/wecom) 与 `contact`
  (外呼取任务联系人, 来电取弹窗识别的主叫人); 旧库自动迁移补列。
- **自动生成摘要**: `对方: <首句> | AI回复 N 次`。

### 使用
```bash
# 1. 正常跑桥接, 记录自动产生
.venv/Scripts/python.exe bridge.py

# 2. 查看记录页面
.venv/Scripts/python.exe calllog_app.py        # http://127.0.0.1:8766
#   /                通话列表 (时长/事件数/摘要)
#   /call/<call_id>  对话时间线 (对方/AI/工具气泡)
```
配置: `.env` 中 `CALLLOG_ENABLED=1`(默认) / `0` 关闭。

## 自动拨号模块 (模块2)

UI 自动化驱动 PC 端应用发起语音通话, 支持单个/批量, 任务内容自动注入给 AI。
拨号/接听/挂断的 UI 细节由 `adapters` 包按应用配置驱动 (窗口标题/按钮名/快捷键),
校准数据按应用分存 (`data/autodial_calib_<app>.json`)。

### 微信 4.1+ 视觉方案 (2026-08-10 实测 @ 微信 4.1.12.26)

微信 4.1 起主窗口内容区改为 MMUI 自绘渲染, **UIA 控件树为空**, 旧的按钮名定位全废。
`adapters/wechat.py` 设 `ui_engine="vision41"`, 由 `autodial/wx41.py` 走纯视觉方案:

| 步骤 | 实现 |
|---|---|
| 激活微信 | FindWindow(Qt51514QWindowIcon)+SetForegroundWindow, 带重试 |
| 进入通讯录 | 左栏图标: 模板 `data/wx41_contacts.png` 匹配, 回退坐标 (45,324) |
| 精确搜索 | 搜索栏模板匹配/坐标 → 剪贴板粘贴 → **OCR 校验结果** |
| 同名重复 | OCR 多条完全一致 → 终止该通, 记入通话记录"xxx名称重复, 未执行呼叫" |
| 打开会话 | 点击完全命中第一条 |
| 拨号 | 右上【语音通话】模板匹配/坐标 → 菜单第一项(第二项是视频, 勿点) |
| 挂断 | 通话窗口**大红色圆圈** (红色连通域, 面积≥1500 且近圆形) |
| 接听 | 来电窗口**大绿色圆圈** (同原理); 联系人名称用 OCR 识别 |

依赖新增 `rapidocr-onnxruntime` (已入 requirements.txt)。

### 企业微信视觉方案 (2026-08-10 实测 @ WXWork.exe)

企业微信主窗口 class=`WeWorkWindow`、title=`企业微信`, UIA 树同样只有 4 个空 Pane,
`adapters/wecom.py` 设 `ui_engine="wecom_vision"`, 由 `autodial/wecom_ui.py` 走视觉方案:

| 步骤 | 实现 |
|---|---|
| 激活应用 | FindWindow(WeWorkWindow)+SetForegroundWindow, 带重试 |
| 进入通讯录 | 左栏第 10 项, OCR 找【通讯录】文本行自校正, 回退坐标 (135,738) |
| 精确搜索 | 点击搜索框 (OCR "搜索"≈(428,75)) → Ctrl+A/Delete 清除 → 剪贴板粘贴 |
| 结果判定 | 全窗口 OCR, 中间列表区 (100<cy<600, 320<cx<1000) 精确命中; 多条同名→终止 |
| 打开客户 | 点击查询结果第一条 |
| 拨号 | 客户面板【语音通话】OCR 优先 (实测 (1739,658), 像素验证过按钮底色), 坐标回退 |
| 挂断 | 通话界面下方**大红圆** (红连通域), 回退 OCR【挂断】文本 |

实测注意: 点击左上角头像区会弹出账号菜单且**点击窗口其它区域不会关闭**,
只能 Escape (会隐藏主窗口, 需重新激活) —— 因此坐标必须用 OCR 实测值,
勿凭截图预览目测 (预览有非等比缩放, 目测坐标全错)。

### 工作流程
1. **校准一次** (每个应用各做一次): 记录语音通话按钮位置(鼠标悬停 + Ctrl+C 捕获, 自动截按钮模板图)
2. **拨号**: 激活应用窗口 → 搜索快捷键 → 剪贴板粘贴联系人名(中文安全) → Enter 开会话
   → 模板匹配优先、窗口偏移坐标回退点击拨号键
3. **任务注入**: 拨号前写 `data/current_task.json`, bridge 轮询到后动态 `session.update`
   把"★核心目的 + ★开场白"结构化拼进 instructions(system prompt); 外呼接通数秒无人出声时 AI 主动先开口
4. **批量**: 等上一通真正结束(读 calllog)再拨下一个, 带超时保护

### 开场白 (开场第一句话)

- **来电**: 固定文案, `.env` 的 `INCOMING_GREETING` 配置; AI 自动接听后第一句说它。
- **外呼**: 由任务发起人指定——CLI `--opening` / tasks.json 的 `opening` 字段 / Web 表单;
  未指定时回退 `.env` 的 `OUTBOUND_DEFAULT_OPENING`。
- 开场白写进 **instructions(system prompt)**, AI 在接通 seed 触发后第一句说出开场白,
  随后围绕"★核心目的"主动继续推进对话。

### 使用
```bash
# 0. 探测窗口, 确认能匹配到目标应用 (钉钉/企微实测的第一步!)
.venv/Scripts/python.exe -m autodial.cli windows

# 1. 一次性校准 (--app 指定应用, 需该应用已登录且主窗口打开)
.venv/Scripts/python.exe -m autodial.cli calibrate --app wechat
.venv/Scripts/python.exe -m autodial.cli calibrate --app dingtalk
.venv/Scripts/python.exe -m autodial.cli calibrate --app wecom

# 2. 单个拨号 (--opening 指定 AI 接通后第一句话; 缺省用 OUTBOUND_DEFAULT_OPENING)
.venv/Scripts/python.exe -m autodial.cli dial 张三 --task "回访确认收货"
.venv/Scripts/python.exe -m autodial.cli dial 张三 --task "回访确认收货" --opening "张总您好, 我是XX公司的小李"
.venv/Scripts/python.exe -m autodial.cli dial 张三 --app dingtalk --dry-run

# 3. 批量拨号 (--opening 为批次共享开场白; tasks.json 单条可自带 opening 覆盖)
#    names.txt 一行一个名字(共享 --task); 或 tasks.json: [{"contact":..,"task":..,"opening":..}]
.venv/Scripts/python.exe -m autodial.cli batch names.txt --task "统一通知"
.venv/Scripts/python.exe -m autodial.cli batch tasks.json --app wecom --opening "您好, 打扰一分钟"

# 4. 或者用 Web 界面 (应用选择器 + 单个/批量 + 任务状态)
.venv/Scripts/python.exe autodial_app.py          # http://127.0.0.1:8767
```

> 注意: UI 自动化对应用版本/窗口布局敏感, 换版本或界面变化需重新 `calibrate`。
> 钉钉/企业微信的窗口标题/按钮名为**尽力猜测**, 务必先用 `windows` 命令探测真实控件名,
> 再按需修改 `adapters/dingtalk.py` / `adapters/wecom.py`; 若 UIA 抓不到控件(自绘界面),
> 退路是 `--manual` 纯音频模式手动接拨。
> 拨打时勿动鼠标键盘; 批量拨打依赖 bridge.py 同时在运行(通话记录用于判断上一通是否结束)。

## 来电自动接听 & AI 挂断 (模块2 增强)

至此实现**全无人值守闭环**: 来电自动接 → AI 对话 → AI 判定结束自动挂断。

### 来电自动接听
- `bridge.py` 启动后内置 `IncomingWatcher` 轮询线程: 扫描所有 UIA 窗口,
  发现含"接听/接受"按钮的弹窗即判定来电, 自动 UIA 点击接听。
- 接通后 3 秒若无人出声, AI 主动先开口打招呼 (复用 opening-seed)。
- 视频来电默认跳过 (弹窗文案含"视频"), `AUTO_ANSWER_VIDEO=1` 可放开。
- 防误触: 通话中/刚挂断自动进入检测冷却期。

### AI 挂断 (hang_up tool)
- `hang_up` 工具随 session 注册给模型: 对方明确说"挂了/拜拜/先这样"时 AI 自主调用。
- 时序: 调用 → 回传结果 → 模型生成道别语 → **等 farewell 完整播给微信** → 点击挂断按钮
  → 收尾通话记录。全程上行音频静默, 防止误触发新回复。
- 挂断按钮定位: UIA 找"挂断/结束"按钮优先, 校准坐标/模板回退。
- 挂断原因与过程写入通话记录时间线 (note 事件)。

### 配置
```
AUTO_ANSWER=1            # .env, 或 bridge.py --no-auto-answer 临时关闭
AUTO_ANSWER_VIDEO=0      # 只接语音 (推荐)
AUTO_ANSWER_POLL=1.0
```

## 语音消息模块 (模块4: voice_msg / sendvoice)

给指定联系人**发送语音条** (不是语音通话)。2026-08-12 实测通过 @ 微信 4.1:
微信语音消息是"本地录音→自行编码上传", 录音吃**系统默认麦克风**——
切默认麦到 CABLE Output 再往 CABLE Input 播音频即可, **全程不需要转 silk**。

- **四种输入**: 文本(TTS 合成) / .wav / .silk(微信语音, silk-wasm 解码) / .mp3 等(ffmpeg 解码)
- **TTS 双引擎**: 主引擎 volc 豆包 Seed TTS (HTTP); 失败自动降级 **edge-tts 免费兜底** (无需 key, `TTS_FALLBACK_ENABLED=0` 可关)
- **铁律**: 音频准备(TTS/解码)失败直接报错退出, **绝不触碰微信**
- **安全**: OCR 分区定位联系人 + 会话标题校验, 防误发; >60s 拒发(微信上限)
- **时序**: 切麦 → 点绿色语音按钮(发送键左侧) → 等 250ms 播音频 → 播完等 250ms → 点绿箭头发送

> 易混淆: 输入区工具栏中部话筒图标是"语音输入法"(ASR转文字), Ctrl+Win 是全局语音转文字, 都**不是**发语音条。

### 使用

> 提示: 项目目录已加入用户 PATH, **新开的**终端窗口可直接敲 `sendvoice`;
> 当前旧窗口 PowerShell 不搜当前目录, 需写 `.\sendvoice ...`。

```bash
# 项目根目录 sendvoice.cmd 可任意目录调用:
sendvoice 小芳 "D:/a.silk"                    # 发 silk
sendvoice 小芳 "D:/a.mp3"                     # 发 mp3
sendvoice 小芳 --text "明天上午十点开会"        # 文本 -> Seed TTS -> 语音条
sendvoice 小芳 "D:/a.wav" --dry-run           # 只准备音频不操作微信

# 模块内使用:
from voice_msg import send_voice_msg
send_voice_msg("小芳", source="D:/a.silk")
send_voice_msg("小芳", text="明天上午十点开会")
```

### 配置 (.env)
| 变量 | 说明 |
|---|---|
| `TTS_ENGINE` | 主引擎: `volc` (默认) / `edge` (直接用免费引擎) |
| `TTS_FALLBACK_ENABLED` | volc 失败自动降级 edge (默认 1, 设 0 关闭) |
| `TTS_EDGE_VOICE` | edge 音色, 默认 `zh-CN-XiaoxiaoNeural` (`sendvoice --list-voices` 查全部) |
| `TTS_ENABLED` / `TTS_TIMEOUT` | Seed TTS 开关 / HTTP 超时秒 |
| `TTS_API_KEY` / `TTS_BASE_URL` | 火山方舟 key / OpenAI 兼容 speech 端点 |
| `TTS_MODEL` / `TTS_VOICE` / `TTS_SPEED` | 模型 / 音色 / 语速 |
| `VOICE_REC_START_MS` / `VOICE_TAIL_MS` | 开录音后延迟播 / 播完延迟发送 (毫秒, 默认 250/250, 调参用) |
| `FFMPEG_PATH` / `SILK_NODE_EXE` / `SILK_NODE_MODULES` | 外部工具路径 (一般自动检测无需改) |

依赖: silk 解码需 node 环境装 `silk-wasm` (已装在托管 node workspace,
`tools/silk_decode.js` 自动调用); mp3 等需 ffmpeg (`D:\programes\ffmpeg` 已就绪)。

## 已知限制 (原型阶段)

- 重采样是线性插值, 音质够用但不完美; 追求音质可换 `soxr`。
- 物理扬声器外放会把你房间的环境音也捕获进去, 建议戴耳机或安静环境;
  若需完全隔离可改为"应用扬声器→CABLE Input + 程序同时把 CABLE 内容转发到物理扬声器监听"。
- UI 自动化(拨号/接听/挂断)基于 pywinauto+pyautogui, 依赖应用窗口布局与按钮文案,
  换版本需重新校准; 若挂断按钮 UIA 找不到, 需在 calibrate 里补 `hangup_offset`。
- 单实例单通话: 音频路由全局(一块扬声器/一条 CABLE/一路 Realtime 会话),
  不支持多应用并发多路通话。
- 钉钉/企业微信的 AEC/降噪行为未实测, 可能抑制 AI 音频, 需真机验证。
