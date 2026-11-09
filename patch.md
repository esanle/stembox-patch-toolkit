# patch.md — Stembox 授权补丁技术文档

对象：**Stembox 2.0.0**（arm64 Mach-O，Qt 6.8.3，原厂 MIXYSTORE Developer ID 签名）
产物：`build/app-patched/Contents/MacOS/Stembox`（5 处指令级改动 + ad-hoc 重签）
脚本：`apply_patches.py`（唯一维护入口，幂等）

---

## 1. 授权链路（逆向结论）

```
启动
 └─ sbx::substrat::licence::franchirLaPorte()            ← 授权总闸
     ├─ LicenseGate::resolveStatus()                    ← 判状态，产出 GateResult
     │   ├─ loadStore()      读 stembox_license.json
     │   ├─ 若 ok==false     → kind=trial（试用路径）
     │   └─ 若 ok==true      → verifyToken(token, deviceId)
     │                         ├─ 拆 '.' → base64url(payload) + base64url(sig)
     │                         ├─ crypto_ed25519_check(sig, payload, 内嵌公钥)
     │                         └─ 成功后取 d/tier/exp/iat/kind 并核对
     ├─ status != 0 → 弹 DlgLicense 模态框（"Your 7-day free trial has ended"）
     └─ trial::setResolved(bool) + 写 licenceDeveloppeur 字节
运行期
 └─ trial::isTrial() → 1 则 installLockOverlay() 盖 UI、拒绝 stems 分离
```

**状态枚举**（`resolveStatus` 里按 kind 分派，实测）：blocked = `trial` / `trial_expired` /
`invalid` / `disabled` / `trial_device_used`，其余放行。

**token payload**：`{d, tier, exp, iat, kind}`；`d` = 本机 `IOPlatformUUID`
（`ioreg -rd1 -c IOPlatformExpertDevice` 正则提取）；`exp > now`；`iat ≥ 1` 且 `|now-iat| < 86400s`。
成功验证后 token 被**清空**（一次性），状态落盘。

**防倒拨**：`clockHighWater = max(历史, now)`（文件 + plist 各一份），倒系统时钟无效。

---

## 2. 五处补丁

| # | 位置（符号 / 指令上下文） | 原指令 | 改后 | 效果 |
|---|---|---|---|---|
| **P1** | `stembox::trial::isTrial()` 入口 | `tbnz w8,#0, →cached` (`081d0037`) | `nop` (`1f2003d5`) | 恒走完整计算路径 |
| **P2** | 同函数尾部（epilogue 前） | `ldrb w0,[x20,#0xe43]` (`800e7939`) | `mov w0,#0` (`00008052`) | 永远返回"非试用" → 锁 UI / 锁 stems 全失效 |
| **P3** | `LicenseGate::verifyToken()`，`bl _crypto_ed25519_check` 之后 | `cbz w0, →成功分支` (`c0040034`) | `b →成功分支` (`26000014`) | 跳过 Ed25519 验签 |
| **P4** | `licence::franchirLaPorte()`，`bl resolveStatus` 之后 | `cbz w8, →跳过弹窗` (`e8020034`) | `b →跳过弹窗` (`17000014`) | 永不弹激活框 |
| **P5** | 二进制内嵌 Ed25519 SPKI 公钥（60 字符 base64） | `MCowBQYDK2VwAyEAeHG7SXjIcM5yomnrnQg39a1q6kiZAR6+GgdT5DOA2QM=` | 我们的 SPKI（等长替换） | 我们签的 token 能走完正常流程 |

### 为什么这样选

- **P1 用 `nop` 而不是 `b`**：早期改成 `b 计算段` 会跳过中间一条栈初始化指令，
  导致 `QDir` 收到野指针 → 启动后随机 SIGSEGV（21:36 崩溃报告）。
  `nop` 保留全部栈语义，只去掉"有缓存就直接返回"的短路。
- **P2 改返回值而不是状态字节**：日志里 `licence is a trial: true` 是**显示用的缓存字节**，
  真正的门是 `isTrial()` 的返回值；改返回值最小、最稳。
- **P3 跳过验签而不是伪造签名**：厂商 token 的**被签消息体始终未逆出**
  （实测过 left-segment / decoded-JSON / empty 三种假设，全部被拒），
  所以走"跳过"而非"伪造"。代价：payload 仍需 JSON 合法、`d` 匹配、`exp/iat` 有效
  （这些检查在 P3 之后，不可跳过）——所以仍需要 `apply_patches.py mint` 产 token。
- **P4 是全场唯一弹窗点**：`DlgLicense` 构造只有一处调用，跳过即彻底静音。

### 偏移表

| 版本 | P1 | P2 | P3 | P4 |
|---|---|---|---|---|
| 1.8.12 | `0x2281c0` | `0x228560` | `0x254d30` | `0x16a80c` |
| **2.0.0（当前）** | `0x2af930` | `0x2afcd0` | `0x2ec4a8` | `0x1a0080` |

1.8.12 与 2.0.0 指令编码完全一致（同编译器模式），只是地址变。

---

## 3. 签名与部署（macOS 特有坑）

1. **改一字节即签名失效** → 必须重签。
2. **`/Applications` 受 provenance 保护，不可原地写**（`cp`/`dd` 都报 Operation not permitted）→
   固定流程：**拷出 → 改 → 重签 → 拷回覆盖**。
3. **hardened runtime 会触发 library validation**：主程序改 ad-hoc 后，
   同为 ad-hoc 的 Qt frameworks 会因 "different Team IDs" 被 dyld 拒绝加载
   （崩溃报告 `dyld4::halt` / `Library not loaded: @rpath/QtWidgets.framework`）。
   → 重签**不要加 `--options runtime`**：`flags=0x2`（而非 `0x10002`）。
4. ad-hoc 签名无公证：首次启动 Gatekeeper 可能提示"未知开发者"，点 Open 一次即可。

### 部署命令

单文件工具一把梭（patch → 重签 → 部署 → 装 token）：

```bash
cd /Users/eddie/Projects/rea-stembox
python3 apply_patches.py install-app --tier lifetime
```

等价的手工步骤：

```bash
python3 apply_patches.py patch                    # 打补丁（幂等）
codesign --force --sign - build/app-patched/Contents/MacOS/Stembox
rm -rf /Applications/Stembox.app
cp -Rp build/app-patched /Applications/Stembox.app
xattr -dr com.apple.quarantine /Applications/Stembox.app
```

### 工具子命令一览

| 子命令 | 作用 |
|---|---|
| `patch` | 打 5 处补丁（`--verify-only` 干跑，`--binary` 指定目标） |
| `keys` | 生成我们的 Ed25519 密钥对（`build/keys/keys.json`，权限 600）；`--force` 覆盖 |
| `mint` | 签一个 token（`--tier` / `--device` / `--exp-days` / `--out`） |
| `install` | 签发并写入 app 的 license store（含 `ok:true`、`lastCheck` 刷新） |
| `install-app` | patch + 重签 + 部署到 /Applications + 装 token |
| `status` | 一屏看清：密钥、pristine/patched 的补丁状态、已安装 app 状态、license store |

### token 安装（每次现签）

```bash
python3 apply_patches.py install --tier lifetime
python3 - <<'EOF'
import json, time
p = "/Users/eddie/Library/Application Support/stembox-substrat/stembox_license.json"
d = json.load(open(p))
d["token"] = open("build/keys/token.txt").read().strip()
d["lastCheck"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
d["ok"] = True
json.dump(d, open(p, "w"))
EOF
/Applications/Stembox.app/Contents/MacOS/Stembox &
```

---

## 4. 验证清单

| 检查 | 期望 |
|---|---|
| `python3 apply_patches.py --verify-only` | 全 `already patched`，退出 0 |
| 4 个 patch 位点字节 | `1f2003d5` / `00008052` / `26000014` / `17000014` |
| `grep -c MCowBQYDK2VwAyEAA/fLZ5 build/app-patched/.../Stembox` | 1 |
| `codesign -dvvv .../Stembox` | `flags=0x2(adhoc)`，`Signature=adhoc` |
| 启动后窗口列表 | 有 `Stembox` 主窗口，**无** `Stembox — Activation` |
| `~/Library/.../stembox-substrat/logs/stembox.log` | 无崩溃、`licence is a trial` 仅为显示缓存 |

## 5. 升级到新版本

1. 取新版 DMG：`https://stemboxdj.com/downloads/Stembox-<ver>-arm64.dmg`
   （或更新接口 `software_versions?platform=eq.mac_arm`，带 `apikey: sb_publishable_…`）。
2. 挂载 → `cp -Rp` 到 `build/app-pristine/` → `cp -Rp` 到 `build/app-patched/`。
3. `nm -gU <bin> | c++filt | grep` 确认符号仍在：
   `trial::isTrial`、`LicenseGate::verifyToken`、`licence::franchirLaPorte`。
4. `otool -tv <bin>` 按**指令模式**（与地址无关）重新定位 4 个偏移：
   - P1：`isTrial` 入口 `tbnz w8,#0, →cached`
   - P2：`isTrial` 尾部 `ldrb w0,[x20,#off]`，其后是 epilogue + `ret`
   - P3：`bl _crypto_ed25519_check` 的**下一条** `cbz w0, →success`
   - P4：`franchirLaPorte` 里 `bl resolveStatus` 的**下一条** `cbz w8, →skip`，
     且其后紧跟 `bl DlgLicense::DlgLicense`
5. 填进 `apply_patches.py` 的 `PATCHES` 表（脚本会校验原字节，不符即 abort，不会误写）。
6. 若官方换了 Ed25519 公钥：从新二进制里找 SPKI base64（形如 `MCowBQYDK2VwAyEA…`，
   44 字节 DER → 60 字符 base64），`apply_patches.py keys --force` 重新生成密钥对，
   再跑 `apply_patches.py patch`（P5 会写入新公钥）。

## 6. 未解 / 风险

- **未解**：厂商 token 的被签消息体（三种假设均失败）；PQT2 的 aux 字节语义（见独立项目 `source/docs/EVIDENCE.md`）。
- **更新即失效**：官方发布新版会覆盖 `/Applications` 里的补丁，需重走 §5。
- **验签被跳过**：仅影响"能否伪造厂商 token"，不影响正常功能；
  但 P3 之后的 payload 校验（device 匹配、有效期）仍在生效。
- **仅本机可用**：密钥在 `build/keys/keys.json`（git-ignore，权限 600）；私钥已被 `.gitignore` 排除，
  不要提交，也不要用于其它产品。
