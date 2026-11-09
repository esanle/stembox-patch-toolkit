# Stembox 1.8.12 — 逆向调查报告

目标:`/Applications/Stembox.app`(Mach-O arm64,包体 147 MB)
方法:REA 6.5.0 CLI + 常规静态工具
日期:2026-10-16

**证据分级说明。** 本报告严格区分两类来源:
- **[REA]** = REA 产出的正式 Evidence(带 evidence_id)
- **[静态]** = 常规 shell 工具观测(file / otool / codesign / nm / c++filt / strings / plutil),**不构成 REA Evidence**

原生反编译本次**未能执行**(Hopper 被 macOS 自动化权限拦截,详见 §7)。因此下述实现细节以符号表、内嵌字符串与随包资源为依据,凡属推断均已标注。

---

## 1. 目标身份

| 项 | 值 | 来源 |
|---|---|---|
| Bundle ID | `com.stembox.stembox` | [静态] `plutil` |
| 版本 | 1.8.12 | [静态] `plutil` |
| 类别 | `public.app-category.music` | [静态] `plutil` |
| 版权 | (c) Mixystore | [静态] `plutil` |
| 最低系统 | macOS 14.0 | [静态] `plutil` |
| 架构 | Mach-O 64-bit **arm64**(thin,非通用二进制) | [静态] `lipo`/`file` |
| 签名 | Developer ID Application: **MIXYSTORE (LX3L4NVBZJ)** | [静态] `codesign` |
| 公证 | Notarization Ticket=stapled,Hardened Runtime | [静态] `codesign` |
| 主二进制 | 9,427,856 B,**未 strip**(5679 个已定义代码符号) | [静态] `nm` |
| 包根 SHA-256 | `55ce2502f566c9537ef725627a802a48a13c46fbf081533f703ada065d01ef85` | **[REA]** `inspect-artifact` |

**[REA]** 包清单证据:`evidence_id = ev_3cc8616283e61ecb8fbfcc66ef31193aca9c56504d538490f998f1462e5c42dc`,provider `rea-artifact-graph`,
manifest `agm_149d6f96937c502d5feb98c0a501711de2ef3dd057d71b4016687dad4101e167`,
**406 节点 / 508 引用 / 477 边**。完整 JSON 存于 `evidence/inspect-artifact.json`。

**一句话结论:** 这是一个**原生 Qt6/C++ 桌面 DJ 软件**(非 Electron),以 Mixxx 风格皮肤系统承载界面,自研音频引擎与控制器层,内置 ONNX 音轨分离,并直接读写 rekordbox 库、能导出 Pioneer CDJ 可读的 U 盘。

---

## 2. 技术栈(版本均经二进制实测)

### 2.1 应用框架与语言

| 组件 | 版本 | 说明 |
|---|---|---|
| **Qt** | **6.8.3** | 动态链接,QtWidgets/Gui/Core/Svg/Xml/Concurrent/**Sql**/**Network**/Core5Compat |
| C++ 标准库 | libc++ (`std::__1`) | 符号内可见 |
| 构建系统 | **CMake** | 内嵌路径 `/build-mac/_deps/...`(FetchContent) |

Qt 插件实测:`platforms/libqcocoa`、`styles/libqmacstyle`、`sqldrivers/{sqlite,mimer,odbc,psql}`、
`tls/{openssl,securetransport,certonly}`、`imageformats/{jpeg,gif,ico,svg}`、`iconengines/libqsvgicon`。 [静态]

### 2.2 音频与 DSP

| 组件 | 版本 | 用途(推断/实测) |
|---|---|---|
| **FFmpeg** | **8.1** | 解码 + 写 `.stem.mp4`;`libavcodec 62.28.100` / `libavformat 62.12.100` / `libavutil 60.26.100` / `libswresample 6.3.100` |
| **ONNX Runtime** | **1.22.0** | 音轨分离推理 |
| SoundTouch | 2.4.1 | 变速变调 |
| Signalsmith Stretch / Linear | `a670068d` / `146b26f9` | 母带级时间伸缩(仅头文件,静态编入) |
| TagLib | 2.3.1 | 音频元数据 |
| mpg123 | 1.32.10 | MP3 解码 |
| libFLAC | 1.4.3 | FLAC 解码 |
| hidapi | 0.15.0 | DJ 控制器 HID 通道 |
| ASIO SDK | 2.3.4 | Steinberg **专有**,Windows 侧声卡后端 |

系统侧:`CoreAudio`、`AudioToolbox`、`CoreMIDI`、`IOKit`、`Metal`/`OpenGL`(经 QtGui)、`libcurl`、`Security`。 [静态] `otool -L`

**FFmpeg 许可声明独立复核。** 随包 `licences/index.txt` 声称"实测"该构建为 LGPL-3.0。核对 `libavcodec.62.dylib` 内嵌构建串:

```
FFmpeg version 8.1
--enable-shared --disable-static --enable-version3 --disable-gpl --disable-nonfree ...
--prefix=/Users/sandydaffy/.stembox-outils/ffmpeg-8.1-macos-arm64
libavcodec license: LGPL version 3 or later
```

**声明属实**,且构建机路径带出开发者用户名 `sandydaffy`。 [静态]

### 2.3 数据与授权

| 组件 | 版本 | 用途 |
|---|---|---|
| **sqlite3mc** | 2.5.1 | 加密 SQLite;实测含 `_sqlite3_activate_see`(SQLite SEE 接口)、AEGIS、miniz、SHA-2 |
| **monocypher** | 4.0.2 | 加密原语,60 个符号,静态链接 → 授权校验(`LicenseGate` / `trial`) |
| **Sentry + Crashpad** | sentry-native 0.16.8 | 崩溃上报;随包 `crashpad_handler` |
| **Kaitai Struct Runtime** | 0.11 | 静态编入,声明式解析 rekordbox **ANLZ** 二进制格式 |
| 后端服务 | **Supabase** | `https://eqilsyogxtjcauubedcc.supabase.co/functions/v1/app-report`、`/rest/v1/settings` |
| 账号 / 分发 | `stemboxdj.com` | `/account`、模型下载源 |

**[静态]** 二进制内嵌 URL 全集(去重后关键项):

```
https://eqilsyogxtjcauubedcc.supabase.co/functions/v1/app-report
https://eqilsyogxtjcauubedcc.supabase.co/rest/v1/settings
https://stemboxdj.com/account
https://stemboxdj.com/downloads/stems-hdemucs-mmi-v1.onnx
https://65390c919420798f2a8021c298f90f3f        ← 32 位十六进制,用途未确认
```

---

## 3. 代码架构

命名空间符号统计 **[静态]** `nm | c++filt`,`sbx::` 2541 个、`stembox::` 342 个。

### 3.1 自研分层 `sbx::`(引擎与基础设施)

| 子系统 | 符号数 | 职责 | 代表类/函数 |
|---|---|---|---|
| `engine` | 450 | 音频引擎 | `Deck`、`Mixer`、`Crossfader`、`MoteurDePlateau`(转盘电机)、`Limiter`、`LevelMeter`、`GeometrieDePlateau`(转盘几何)、`FiltreAlphaBeta`、`dureeDuBackspin` |
| `controllers` | 252 | 控制器接入 | `DeviceDiscovery`、`decodeMidiInput`、`decodeHidLearningReport`、`FeedbackBridge`、`ClientTranslationTable`、`classifyControl` |
| `library` | 216 | 曲库 | `Database`、`LazyTrackModel`、`LibraryViewModel`、`HotcueLocalStore`、`GrilleLocalStore`、`parseKeyName`、`circleOfFifthsRank`(五度圈调性) |
| `mappingeditor` | 195 | 映射编辑器 + 学习 | `apprendreLigne`、`applyLearnedBinding`、`evaluateLearningClassifier`、`AssistantRotation` |
| `audio` | 160 | 声卡抽象 | `AudioManager`、`CoreAudioBackend`、`ClockDriftCorrector`、`ClockDriftResampler`、`DeviceRateCoordinator` |
| `analysis` | 69 | 离线分析 | `analyzeTrack`、`ShortTimeSpectrum`、`hannWindow`、`decideKey`、`majorReferenceProfile`/`minorReferenceProfile`(调性识别) |
| `cablage` | 54 | "接线":引擎↔硬件 | `detournerLesJogs`、`installerPlateaux`、`verbesDuMoteurReel` |
| `rekordbox` | 52 | rekordbox 互操作 | `readDatabase`、`readAnalysis`、`loadTrackAnalysis`、`mergeExtendedAnalysis`、`AccessJournal`、`ColourTable` |
| `control_neuf` | 35 | 新版控制注册表(CH-03) | `Registry`、`Subscription`、`ButtonEvent`、**`readCellLockFree`**(实时音频线程无锁读) |
| `storage` | 31 | 卷级存储 | `LocalStore`、`StoreLock`、`VolumeKey` |
| `retour` / `retourmoteur` | 19 / 13 | 反馈回路 LED / 电机 | `RetourLed`、`ConfirmationMoteur`、`lampesDeBoucle` |
| `exportcle` | 13 | **U 盘导出** | `ecrireExportPdb`、`ecrireExportExtPdb`、`ecrireOneLibrary`、`chaineDeviceSql`、`calculerOndesPioneer` |
| `casque` | 11 | 耳机监听 | `NiveauCasque` |
| `application` | 102 | 装配 | `AudioEngineAssembly`、`PlaybackActivityWakeLockBridge` |

### 3.2 品牌层 `stembox::`

| 子系统 | 符号数 | 说明 |
|---|---|---|
| `stems` | 142 | 音轨分离全流程 |
| `substrat` | 105 | 底层装配 |
| `DlgLicense` / `LicenseGate` / `trial` | 17 / 13 / 6 | 授权闸门与试用 |
| `devicediag` / `sounddiag` | 15 / 4 | 设备与声音诊断 |
| `UpdateChecker` / `DlgUpdate` | 9 / 7 | 更新检查 |
| `ui` / `theme` | 11 / 11 | 界面与主题 |

### 3.3 界面层(Mixxx 同源命名)

`WLibrarySidebar`(77)、`WTrackTableView`(43)、`WStemboxLibrary`(41)、`SidebarModel`(23)、`WWaveformBox`(21)、
`SkinContext`(54)、`DlgPrefDisplay`/`DlgPrefSound`/`DlgPrefMixer`/`DlgPrefPadFx`、`UserSettings`、`ControlObject`。 [静态]

---

## 4. 关键实现细节

### 4.1 音轨分离(Stems)

```
模型   : https://stemboxdj.com/downloads/stems-hdemucs-mmi-v1.onnx   (HDEMUCS)
运行时 : onnxruntime 1.22.0
产物   : .stem.mp4 与 .stem.m4a      ← Native Instruments Stems 容器格式
体积   : 首次安装约 480 MB
```

**[静态]** 相关日志字符串揭示了运行时行为:

- `stems: a deck started playing - stopping the separation, it will resume when the decks stop`
  → **播放期间暂停分离**,避免与实时音频线程抢 CPU。
- `stems queue: separation model not installed (...)`、`stems: downloading the separation model from`
  → 模型按需下载,入队(`Queue`、`PendingItem`、`kQueueFileName`)。
- 目标可自动探测:`availableVolumes`、`defaultVolumeOfKind`、`autoDetectDestination`、`forceDestination`
  → 分离结果可直接落到 U 盘/外置卷。
- `partialPathFor`、`bumpGeneration`、`generation`、`hasStems` → 断点续做与版本代次管理。

**未解释项(标注为未知):** 二进制中出现字符串 `onnxruntime-node`(该字面量属 Node.js 绑定包名),但包内**不含**
任何 Node 运行时,`node_modules` 与 JS 文件均无命中。用途未能确定,不据此推断架构。

### 4.2 rekordbox 与 Pioneer 生态互操作

这是本应用最突出的实现特征——它不是"类似 rekordbox",而是**直接读写其数据结构**。

- **库读取:** `sbx::rekordbox::readDatabase` + sqlite3mc(含 SEE 扩展)→ 读取 rekordbox 加密数据库。
- **ANLZ 解析:** Kaitai Struct 运行时静态编入,由 `sbx_rekordbox_anlz_kaitai` 目标承载,源定义 `rekordbox_anlz.ksy`(EPL-1.0)。
- **U 盘导出:** `sbx::exportcle` 可写出 **DeviceSQL `export.pdb` / `exportExt.pdb`** 与 **OneLibrary** 结构,
  并用 `calculerOndesPioneer` 生成 Pioneer 波形预览 → 产出 **CDJ 可直接读取的 U 盘**。

**[静态]** 二进制内嵌的建表语句同时包含两类 schema:

```
应用自有 : library_tracks、library_nodes、library_node_items
rekordbox: album、artist、content、cue、genre、key、label、playlist、
           history、hot、color、image、menu、my、property、recommended、sort ...
```

### 4.3 控制注册表与映射格式

- 控制项采用点分路径,非 Mixxx 的 `[Group],key`:`deck.{d}.transport.playing`、`mixer.crossfader.position`、
  `fx.unit.1.slot.1.next`。规格文档随包:`docs/controles/controles.csv`(CH-03)。
- `sbx::control_neuf` 引入 `readCellLockFree` → 音频实时线程**无锁读取**控制值,是该层的主要设计动机。
- 控制器映射为**自定义文本 DSL**,实测首行 `SBX_CONTROLLER_MAPPING 6`(版本 6),随包 7 份 Pioneer 映射
  (DDJ-400/800/1000/FLX4/FLX6/FLX10/GRV6)。条目形态:

```
modifier "modifier-113" "deck-1-shift" held 0 midi midi-note 0 63 0 0 0
input "input-31" "mixer.head.mix" write absolute linear 1 0 127 -1 1 1 ...
platter-calibration 1 3108 3184 4
```

`platter-calibration` 与 `sbx::cablage::installerPlateaux`、`sbx::retourmoteur` 表明支持**电动转盘**回馈
(电机确认、环路指示灯)。

### 4.4 皮肤与主题

- 皮肤 XML 采用 Mixxx 模式:`<Template>`、`<WidgetGroup>`、`<Connection><ConfigKey>…</ConfigKey><BindProperty>`、
  `<SizePolicy>me,me</SizePolicy>`,由 `SkinContext` 在运行时解释。
- 随包 29 份布局 XML(非客户原件,见 §5),`stembox-racine.xml` 为拼装根。
- 主题 `style-stembox.qss` 头部自述来源:
  `Source: res/skins/LateNight/style.qss`、`Fork: 4ae413db`、`Mesure: +826 / -2 sur 1894`。
- 品牌字体 `MRRT Stembox.ttf`、`DHA Stembox.ttf`、`HVTC Stembox.ttf` + OpenSans。

---

## 5. 来源与合规观察

随包资源显示出**"部分移交"**的痕迹,值得单独指出:

- `stembox-racine.xml` 开头注明:"由 Atelier 4 编写,**不属于客户**",用于拼合未移交的真实 `skin.xml` 根;
  并声明"真正的根到达之日,本文件即废弃"。
- 多处标注 `StemboxMainWindow`、`skin.xml` **未被移交**。
- `MacOS/` 下存在两个零字节锁文件:`.stembox-deposer-dossier.lock`、`.stembox-licences.lock`。
- `licences/index.txt` 为法语叙述式台账,逐条记录许可义务与"measured on the binary"的核验过程。
- 文档引用 `docs/CHANTIERS/CH-30-preferences-cycle-de-vie.md` 等编号工单。

**Mixxx 血缘 —— 推断与保留。** 界面皮肤层源自 Mixxx 的 `LateNight` 皮肤(证据见 §4.4:皮肤 XML schema、
`res/skins/LateNight/style.qss` 路径、`LateNight` 目录与皮肤名、`SkinContext`/`ControlObject` 及 `W*` 类名)。
**反向证据:** 二进制中 `mixxx` 命名空间、`EngineMixer`、`SoundSource`、`TrackPointer` 等 Mixxx 核心符号**全部为 0 命中**,
故不能判定音频引擎同为 Mixxx 派生——引擎部分(`sbx::engine`)看起来是自研。

**未决合规问题(不作结论,仅记录):** Mixxx 采用 GPL-2.0-or-later;而 `licences/index.txt` 明确声称
"GPL-3.0 出现在此仅因 LGPL-3.0 引用之,**并非本产品有任何部分采用 GPL**",且台账中**未列出 Mixxx**。
皮肤层与 Mixxx 的关系是否产生 GPL 义务,属需法务判断的问题,逆向分析不能定论。

---

## 6. 方法、证据与局限

### 已获得的 REA Evidence

| 操作 | 结果 |
|---|---|
| `doctor --json` | Node 24.18.0 / host 27.0.1 健康;Hopper 健康;Ghidra 存在但版本与架构漂移 |
| `inspect-artifact` | ✅ `ev_3cc8616283e6…` — 406 节点 / 508 引用 / 477 边 → `evidence/inspect-artifact.json` |

### 未能执行的 REA 操作及原因

| 操作 | 失败码 | 原因 |
|---|---|---|
| `inspect`(深度分析) | `provider_unavailable` | macOS **拒绝向 Hopper 发送 Apple Events**(TCC 自动化权限) |
| `inspect-native-load-image` | `capability_unavailable` | Hopper provider 未声明该操作 |
| `inspect-binary-layout` | `unsupported_provider` | pwntools 配置仅支持 Linux x64,不适用于 Mach-O |

### 修复 Hopper 阻断(需人工 GUI 操作)

> 系统设置 → 隐私与安全性 → **自动化** → 允许启动 REA 的应用(终端 / DeepSeek Harness)控制 **Hopper Disassembler**。

修复后即可运行 `rea analyze` / `decompile` / `function` 获取反编译级证据(本轮所有实现细节届时可升级为 REA Evidence)。

**Ghidra 备选路径当前不可用:** 已装 11.4,REA 要求 12.1.x;且 JVM 报告 `os.arch=x86_64`,
与 arm64 主机的原生反编译器 `mac_arm_64` 不匹配(需将 `JAVA_HOME` 指向 arm64 JDK)。

### 局限

- 未 strip 的符号表使类结构清晰,但**函数体行为均未经反编译确认**;§4 中的运行时行为(如"播放时暂停分离")
  读自**日志字符串**,属开发者自述性质,非执行观测。
- 未做运行时观测(无 Electron/浏览器目标;进程捕获与 AX 场景未在本轮授权范围内使用)。
- 模型文件(`.onnx`)在运行时下载,不在包内,其内部结构未检视。
