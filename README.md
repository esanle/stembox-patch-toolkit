# Stembox 授权补丁

对 Stembox 的主二进制（arm64 Mach-O）打 5 处补丁，绕过试用授权，让 app 以完整功能运行。
当前脚本对应 **Stembox 2.0.0**。

> **详细技术文档见 [`patch.md`](patch.md)**（原理、偏移表、签名坑、升级指南、验证清单）。

## 0. 目录布局

```
rea-stembox/                ← 本仓库：仅补丁工具链
├── apply_patches.py     单文件工具：patch / keys / mint / install / install-app / status
├── patch.md             技术文档
├── README.md            本文件
└── build/               ← 全部中间产物（git-ignored，可随时重建）
    ├── app-pristine/    官方原版 2.0.0（未改）
    ├── app-patched/     已打补丁（部署源）
    ├── keys/            我们的 Ed25519 密钥对（私钥绝不提交）
    ├── models/          分离模型权重（~460MB）
    ├── downloads/       官方 DMG
    ├── cmake/           cmake -S source -B build/cmake
    └── venv/            python -m venv build/venv

source/                   ← 独立项目（开源重实现 ostem），已 git-ignore，
                            单独版本管理，不属于本仓库
```

`build/` 里的东西都可重建：pristine 从官网 DMG 重下，patched 由 `apply_patches.py patch` 生成，
密钥由 `apply_patches.py keys` 生成（**换密钥需重打 P5 补丁**）。

## 1. 授权机制速览（逆向结论）

| 环节 | 实现 |
|---|---|
| 许可证文件 | `~/Library/Application Support/stembox-substrat/stembox_license.json`，字段：`activatedAt/clockHighWater/key/kind/lastCheck/token/ok` |
|  gate 类 | `stembox::LicenseGate`：`resolveStatus()` 判定状态 → `GateResult{status, trial, kind, …}` |
| 离线验签 | `LicenseGate::verifyToken()`：token = `base64url(JSON).base64url(签名)`，Ed25519 验签，公钥硬编码在二进制里（SPKI base64，见 §3） |
| 试用门 | `stembox::trial::isTrial()` 返回 1 即加锁：`installLockOverlay` 盖 UI、`stems: refused, not part of the free trial` 拒绝音轨分离 |
| 弹窗 | `franchirLaPorte()`：status 非 0 就弹模态 `DlgLicense`（"Your 7-day free trial has ended…"） |
| 防倒拨时钟 | `clockHighWater = max(历史, now)`，存文件 + plist 各一份 |
| 更新检查 | `UpdateChecker` 查 Supabase `software_versions` 表（`platform=mac_arm`，`published` 最新一条），2.0.0 即如此发现 |

token payload 字段（已逆向）：`d`（本机 IOPlatformUUID，`ioreg -rd1 -c IOPlatformExpertDevice` 正则提取）、
`tier`（→ 显示的 kind）、`exp`（> now 才有效）、`iat`（须 ≥1 且与 now 差 <86400s）、`kind`。

**未解之谜**：Ed25519 的被签消息体始终没能确定（试过左段/JSON/空消息，全部被拒）。
因此 P3 直接跳过验签而不是伪造签名——见 §3。

## 2. 五个补丁（原理）

| # | 位置（符号） | 原指令 | 改后 | 作用 |
|---|---|---|---|---|
| P1 | `trial::isTrial` 入口 | `tbnz w8,#0,→cached` | `nop` | 强制每次走完整计算路径。**教训**：早期曾改成 `b 计算段`，跳过了一条栈初始化指令，栈垃圾导致 `QDir` 野指针，启动后随机崩溃。`nop` 最安全 |
| P2 | `trial::isTrial` 返回 | `ldrb w0,[x20,#off]`（读缓存的 trial 标志） | `mov w0,#0` | 永远返回"非试用"。所有锁 UI/锁 stems 的 `if (isTrial())` 全灭。注意：日志里仍会打印 `licence is a trial: true`（那只是显示用的缓存字节），返回值恒 0 才是门的依据 |
| P3 | `LicenseGate::verifyToken`，`_crypto_ed25519_check` 之后 | `cbz w0,→成功分支` | `b 成功分支` | 跳过 Ed25519 检查。payload 仍需合法 JSON 且 `d/exp/iat` 通过后继检查 |
| P4 | `franchirLaPorte`，`resolveStatus` 之后 | `cbz w8,→跳过弹窗` | `b 跳过弹窗` | 永不弹 `DlgLicense` 模态框，全场唯一弹窗点 |
| P5 | 二进制内嵌 Ed25519 公钥 | 官方 SPKI base64（60 字符） | 自生成 key 的 SPKI（同样 60 字符，等长替换） | 让 `apply_patches.py mint` 签的 token 走完"消费 token"的正常流程（成功后 token 被清空、状态落盘） |

2.0.0 的指令编码与 1.8.12 **完全一致**（同一编译器模式），只是地址变了。
各版本偏移见 `patch.md` §2。

## 3. 密钥与 token

- `python3 apply_patches.py mint --tier lifetime`：用自有私钥签 `base64url(JSON).base64url(sig)`，字段见 §1，`exp=2100-01-01`，`iat=now`。
  **token 有时效**（`iat` 与启动时刻差须 < 86400s）且成功验证后会被 app 消费清空，所以**每次安装前现签现用**，
  不要复用 `token_fresh.txt` 里的旧 token（它只是手头的一个样例）。
- 自签 token + `ok:true` 装进 license JSON 后，`resolveStatus` 会走"消费 token"分支（token 清空、继续启动）。
  即使验签被 P3 跳过，一个**格式正确、device 匹配、时间有效**的 token 仍是必需的——后继的 JSON 解析 / device 对比 / exp-iat 检查都在 P3 之后，不可跳过。

## 4. 签名问题（为什么必须重签、怎么签）

- 原包是 MIXYSTORE 正式签名 + hardened runtime。改一个字节签名即失效。
- `/Applications` 受 macOS provenance 保护**不可原地写**：流程固定为**拷出 → 改 → 重签 → 拷回覆盖**。
- hardened runtime 会强制 library validation：主程序 ad-hoc 签名后，Qt frameworks（同为 ad-hoc、无 Team ID）会被 dyld 以 "different Team IDs" 拒绝加载。
  解法：重签时**不加 `--options runtime`**（`flags=0x2` 而非 `0x10002`），关掉 library validation。frameworks 保持 ad-hoc 即可。
- ad-hoc 签名无公证：首次启动 Gatekeeper 可能弹"未知开发者"，点 Open 一次即可；**官方一更新就会覆盖补丁**。

## 5. 使用方法

### 新机器（推荐，一条命令）

```bash
git clone <repo> && cd rea-stembox
python3 apply_patches.py bootstrap --version 2.0.0 --tier lifetime
```

自动完成：下载官方 DMG → 取 app → **生成该机专属密钥** → 打补丁 → 整体重签 →
部署 `/Applications` → 签发并安装**该机 device id** 的 token。
实测裸 clone 可直接跑通，部署后 app 正常启动、无授权弹窗。

> token 与机器绑定：app 即使跳过验签（P3）仍会比对 payload 里的 `d`（本机
> `IOPlatformUUID`），所以每台机器各自签发即可，`install` 会自动取本机 ID。

### 本机日常使用

```bash
cd /Users/eddie/Projects/rea-stembox
python3 apply_patches.py install-app --tier lifetime   # patch+重签+部署+装token
/Applications/Stembox.app/Contents/MacOS/Stembox &      # 应：无弹窗、主窗口出现、稳定运行
```

手工等价步骤：

```bash
python3 apply_patches.py patch                    # 幂等：缺啥补啥，字节不对直接 abort
codesign --force --deep --sign - build/app-patched # 整体签（必须 --deep，见 §4）
rm -rf /Applications/Stembox.app && cp -Rp build/app-patched /Applications/Stembox.app
xattr -dr com.apple.quarantine /Applications/Stembox.app
python3 apply_patches.py install --tier lifetime   # 一步签发+写入 license store
```

验证清单：`codesign -dvvv` 看 `flags=0x2`；`apply_patches.py` 复跑应全 `already patched`；
启动后查窗口（应有 `Stembox` 主窗口、无 `Stembox — Activation`）、`stembox.log` 无崩溃。

## 6. 给新版本移植（升级指南）

1. 从官网/更新接口拿新版 DMG（更新接口见 §1；2.0.0 地址形如 `https://stemboxdj.com/downloads/Stembox-<ver>-arm64.dmg`），挂载拷出到工作区。
2. `nm -gU 二进制 | c++filt | grep` 确认符号还在：`trial::isTrial`、`LicenseGate::verifyToken`、`franchirLaPorte`。
3. `otool -tv` 反汇编，按**指令模式**（与地址无关）定位：
   - P1：`isTrial` 入口的 `tbnz w8,#0, →cached`；
   - P2：`isTrial` 尾部 `ldrb w0,[x20,#off]` 后接 epilogue+`ret`；
   - P3：`bl _crypto_ed25519_check` 下一条 `cbz w0, →success`；
   - P4：`franchirLaPorte` 里 `bl resolveStatus` 下一条 `cbz w8, →skip`，且后面紧跟 `bl DlgLicense::DlgLicense`。
4. 把 4 个文件偏移 + 公钥状态填进 `apply_patches.py`，跑一遍（脚本会自动校验原字节，不对就 abort，不会误写）。
5. 若官方换了 Ed25519 公钥：`grep` 旧 base64 确认是否还在；不在就从新二进制里找 SPKI base64（`MCowBQYDK2VwAyEA…`，64 字符原料 → 60 字符 base64），照 §3 重新生成 keypair 并替换。

## 7. 文件清单

| 文件 | 说明 |
|---|---|
| `apply_patches.py` | **唯一维护的补丁脚本**（当前对应 2.0.0；`--verify-only` 可干跑） |
| `patch.md` | **技术文档**：原理、5 处补丁原理/字节、偏移表、签名坑、升级指南、验证清单 |
| `apply_patches.py` | **单文件工具**：patch / keys / mint / install / install-app / status（密钥在 `build/keys/`，git-ignore） |
| `build/` | 中间产物（app-pristine / app-patched / models / downloads / cmake / venv），全部 git-ignored |
| `STEMBOX-REVERSE-ENGINEERING.md` | 早期逆向报告（1.8.12 时代背景资料） |

## 8. 风险与注意事项

- 每次官方更新都要重走 §5（DMG → 移植/复用脚本 → 重签 → 部署）。
- `Kind` 显示仍可能是 `trial`（ cosmetic，门控看的是 `isTrial()` 返回值，已恒 0）。
- 不要在 `/Applications` 里原地改二进制（写不进去）；不要用 `b` 跳过 P1 的栈初始化（血泪教训：用 `nop`）。
- 调试手段：`stembox.log`（`~/Library/.../stembox-substrat/logs/`）、按窗口名查 Quartz 窗口列表（弹窗还在不在一眼便知）、`ls -t …/DiagnosticReports/Stembox-*.ips` 看崩溃。
