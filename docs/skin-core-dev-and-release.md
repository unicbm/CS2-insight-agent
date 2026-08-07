# Insight ↔ skin-core（demo-anyskin）开发与打包手册

本地维护用说明：**不纳入 git 提交约定**（本文件可放在 `docs/`，请勿 `git add` / commit）。

相关实现契约（闭源仓库，更细）：

- `C:\code\CS2-demo-anyskin\docs\SKIN_CORE_IPC.md`
- `C:\code\CS2-demo-anyskin\docs\SKIN_CORE_RELEASE.md`
- Insight 正式 Windows 包：`packaging/windows/RELEASE-WINDOWS.md`

---

## 1. 交互模型（两边各自做什么）

```text
饰品页「保存」
  → Insight 后端 POST /api/demos/{id}/cosmetics/custom-plan
  → 解析 workspace 饰品 → 加密 batch 请求
  → 拉起 skin-core.exe（stdin 管道传随机会话密钥）
  → skin-core rewrite-owned-batch（只改 cache dem）
  → Insight 原子覆盖 cached_path，写 DB 方案；不重跑分析
```

| 侧 | 职责 |
|----|------|
| **Insight（开源）** | UI、slot 映射、demo-cache、`ensure_demo_compatible`、拉起进程、AES 帧、DB `demo_custom_skin_plans` |
| **skin-core（闭源 anyskin）** | 管道鉴权 + 祖先 PE 白名单、解密请求、所有权校验、批量改皮、强制 nametag `CS2 INSIGHT AGENT`、加密响应 |

正式 exe **只暴露** 子命令：`rewrite-owned-batch`。

---

## 2. 本地 Dev：Insight 如何跟 anyskin 交互

### 2.1 推荐目录布局

```text
C:\code\CS2-insight-agent      ← develop
C:\code\CS2-demo-anyskin       ← main（与 insight 同级，便于自动发现）
```

### 2.2 Anyskin 侧需要做的操作（Dev）

在闭源仓库根目录：

```powershell
cd C:\code\CS2-demo-anyskin

# 开发用：允许空 PE 白名单打出 dist（切勿当正式包发出）
powershell -ExecutionPolicy Bypass -File .\scripts\release-skin-core.ps1 -AllowEmptyAllowlist
```

产物：

- `dist\skin-core.exe`（优先被 Insight 发现）
- 或仅 `cargo build --release --bin skin-core` → `target\release\skin-core.exe`

可选自测管道/帧：

```powershell
$env:CS2_SKIN_CORE_DEV = "1"
python .\scripts\dev_launch_skin_core.py
```

说明：

- Dev 时 allowlist 可以是空的；运行时靠 **`CS2_SKIN_CORE_DEV=1`** 跳过 PE 门禁（仍要管道会话密钥）。
- Insight 若解析到的 exe 落在 anyskin 的 `dist/` 或 `target/` 下，会**自动**给子进程加 `CS2_SKIN_CORE_DEV=1`，一般不用你再手动设。
- 也可显式：`$env:CS2_SKIN_CORE_DEV=1` 或 `$env:CS2_INSIGHT_DEV=1`。

### 2.3 Insight 侧发现 skin-core 的顺序

`backend/app/skin_core_client.py`：

1. 环境变量 **`CS2_SKIN_CORE_EXE`**（绝对路径，最稳）
2. 打包资源 `bundle-resources/tools/skin-core.exe`（或 `CS2_INSIGHT_BUNDLE_DATA_DIR` 旁的 `tools/`）
3. 兄弟目录 / 固定路径：
   - `..\CS2-demo-anyskin\dist\skin-core.exe`
   - `..\CS2-demo-anyskin\target\release\skin-core.exe`
   - `C:\code\CS2-demo-anyskin\dist|target\release\skin-core.exe`

Dev 常用两种方式任选：

```powershell
# A. 自动发现（anyskin 已有 dist 或 target\release）
# 什么都不用设，直接跑 Insight

# B. 显式指定
$env:CS2_SKIN_CORE_EXE = "C:\code\CS2-demo-anyskin\dist\skin-core.exe"
```

### 2.4 启动 Insight Dev 后端 / 桌面

按你平时的方式即可（例如 frontend `desktop:dev` 或本地 uvicorn）。确保：

- 已有可解析的 `demoparser2` Python（与现有 demo 解析同一运行时；skin-core 的 `--demoparser2-python` 由后端传入）。
- Demo 已入库且有 `cached_path`；饰品页对该玩家有带 `item_id` 的证据饰品。
- 保存后：只改 cache；原 path 不动；DB 有方案；物品 nametag 为 `CS2 INSIGHT AGENT`。

### 2.5 Dev 排障

| 现象 | 处理 |
|------|------|
| `SkinCoreNotFound` | 先 `release-skin-core.ps1 -AllowEmptyAllowlist`，或设 `CS2_SKIN_CORE_EXE` |
| 子进程 exit 2 | 管道/鉴权失败；确认 Dev 下 exe 来自 dist/target，或设 `CS2_SKIN_CORE_DEV=1` |
| 502 / `ok` 失败 | 看后端日志与 skin-core stderr；核对 item_id64 / steam / definition_index |
| 刀换型号失败（缺少 donor） | 跨刀型要求本场 Demo 里已出现过目标刀型（任意玩家持有即可作 donor）。手套可直接换型号。 |

---

## 3. Anyskin 正式打包（给 Release Insight 用）

**顺序不能反：先有 Insight 正式主程序 PE，再注入白名单，再编 skin-core。**

### 3.1 准备父进程哈希

典型进程链：`CS2 Insight Agent.exe → python.exe → skin-core.exe`  
白名单会沿祖先最多 8 层匹配，**建议同时写入**：

- 正式包里的 `CS2 Insight Agent.exe`（或实际主程序名）
- 同包内会拉起后端的 **bundled `python.exe`**

```powershell
Get-FileHash -Algorithm SHA256 "C:\path\to\CS2 Insight Agent.exe"
Get-FileHash -Algorithm SHA256 "C:\path\to\bundled\python.exe"
```

### 3.2 一键注入白名单并产出 dist

在 `CS2-demo-anyskin`：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\release-skin-core.ps1 `
  -ParentPe @(
    "C:\path\to\CS2 Insight Agent.exe",
    "C:\path\to\bundled\python.exe"
  )
```

- 会先写 `src\parent_allowlist.inc.rs`，再 `cargo build --release --bin skin-core`，再拷到 `dist\skin-core.exe`。
- **不要**对正式包使用 `-AllowEmptyAllowlist`。
- **不要**在正式环境设置 `CS2_SKIN_CORE_DEV`。

### 3.3 加壳（可选，正式建议）

对 `dist\skin-core.exe` 使用 Themida / VMProtect 等（脚本里目前是占位注释，需人工或后续接 CLI）。加壳后仍用该文件作为 Insight 注入源。

### 3.4 冒烟（正式二进制）

| 检查 | 期望 |
|------|------|
| 官方壳 + 白名单内 PE 链 | 换肤成功 |
| 非白名单父进程 / 无管道双击 | exit 2 |
| cache / 原 path / nametag / GET plan | 同规格 |

更细清单见闭源 `docs/SKIN_CORE_RELEASE.md`。

---

## 4. Insight Release 打包时如何带上 skin-core

### 4.1 注入 sidecar（在 `desktop:build:ver` 之前）

`frontend/scripts/stage-tauri-resources.mjs` 会：

- 若设了 **`CS2_SKIN_CORE_EXE`** → 复制该文件  
- 否则尝试 `../CS2-demo-anyskin/dist/skin-core.exe` 等  
- 目标：`frontend/src-tauri/bundle-resources/tools/skin-core.exe`  
- **缺失则跳过**（OSS CI 不失败；正式发布前必须确认已注入）

推荐正式构建前：

```powershell
# 已用正式 PE 白名单编好的闭源产物
$env:CS2_SKIN_CORE_EXE = "C:\code\CS2-demo-anyskin\dist\skin-core.exe"

# 若目录已是 ../CS2-demo-anyskin/dist/skin-core.exe，也可不设环境变量
```

### 4.2 打 Windows 安装包

按 `packaging/windows/RELEASE-WINDOWS.md`：

```powershell
Push-Location frontend
try {
  pnpm.cmd run desktop:build:ver -- 2.x.y   # 换成真实版本号
} finally {
  Pop-Location
}
```

产物示例：

```text
frontend\src-tauri\target\release\bundle\nsis\CS2 Insight Agent_<ver>_x64-setup.exe
```

### 4.3 鸡生蛋问题（白名单 vs 安装包）

父进程哈希来自「本版」主程序 / python。**一键脚本** `build_desktop_with_skin_core.ps1` 现在是三轮：Pass1 拿 PE → 编 skin-core → Pass2 嵌入 sidecar → **按 Pass2 最终 Agent 重编 skin-core → Pass3 再打包并校验白名单**。不要只用 Pass1 哈希出货：嵌入资源后 Agent SHA256 会变（本机曾出现 Pass1=`dfad…`、Pass2=`723d…`），祖先链若读不到 bundled python 就会在数秒内 auth 失败（exit 2）。

手动两轮时也必须以**最终** `cs2-insight-agent-desktop.exe` + bundled `python.exe` 再跑一次 `release-skin-core.ps1 -ParentPe …`，再打安装包。

发布前在干净机器验证：未设 DEV 时换肤可用；换非官方启动器应失败。

### 4.4 正式包内行为

- 使用 `bundle-resources/tools/skin-core.exe` 时，Insight **不会**自动设 `CS2_SKIN_CORE_DEV`。  
- 会话密钥仍每次随机经 stdin 管道传递；规则在加密请求文件内。  
- `custom_name` 由闭源强制写入，Insight 不传。

---

## 5. 速查命令

### Dev（每天）

```powershell
# anyskin
cd C:\code\CS2-demo-anyskin
powershell -ExecutionPolicy Bypass -File .\scripts\release-skin-core.ps1 -AllowEmptyAllowlist

# insight（可选显式路径）
$env:CS2_SKIN_CORE_EXE = "C:\code\CS2-demo-anyskin\dist\skin-core.exe"
# 然后照常 desktop:dev / 后端
```

### Release（发版日）

```powershell
# 1) 有正式 Insight PE 后 — anyskin
cd C:\code\CS2-demo-anyskin
powershell -ExecutionPolicy Bypass -File .\scripts\release-skin-core.ps1 -ParentPe @(
  "...\CS2 Insight Agent.exe",
  "...\python.exe"
)
# 2) 可选加壳 dist\skin-core.exe
# 3) insight
$env:CS2_SKIN_CORE_EXE = "C:\code\CS2-demo-anyskin\dist\skin-core.exe"
cd C:\code\CS2-insight-agent\frontend
pnpm.cmd run desktop:build:ver -- <version>
```

---

## 6. 刻意不做 / 边界

- 开源仓库不包含换肤算法；只调用编译后的 `skin-core.exe`。  
- Dev 空白名单 + DEV 跳过 PE，只为本地联调；正式包禁止 DEV。  
- 借壳改开源脚本仍可能驱动闭源（威胁模型 A+B）；加壳与白名单只抬高成本。  
- 本文件仅本地文档，**请勿提交到 git**。
