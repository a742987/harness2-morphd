# harness2-morphd

> 让 Agent 自己变好，但不打扰你。

harness2-morphd 是一个**本地优先、宿主内运行**的 Agent harness 自动优化产品：照常使用
**dsh / Claude Code / Codex**，每次会话结束后安静总结；当出现失败、反复纠正或工具循环时，
用你自带的模型（BYOK）生成一条可理解、可预算、可撤销的改进建议。默认只建议，不自动改；
所有应用都有快照，所有自动应用都有试用与回滚。

**平台范围：仅 Windows 10 / 11 x64。** 仓库不再提供 macOS / Linux 平台包、安装器或运行时支持。

- **PRD**：[`PRD-v0.5.md`](PRD-v0.5.md)（产品定义；Windows-only 范围以本 README 为准）
- **License**：Apache-2.0。上游 Harness² 保持原样，不修改、不 fork。
- **形态**：宿主 wrapper/hook + 本地 Web UI；NPM 与 Electron 双包共用同一引擎与 UI。
- **不常驻**：没有 daemon、cron、systemd、launchd、开机自启或独立后台队列。

> **交付状态**：Windows 源码、测试、Windows 引擎打包、npm 包和 Electron NSIS/portable
> 安装器均已就绪。产物**未做 Authenticode 签名**；当前发布为未签名正式版，安装时可能出现
> SmartScreen 提示。真实宿主生命周期和真实模型调用仍需在目标机器上验收，未验证项在
> [`docs/verification.md`](docs/verification.md) 中标注 `n/a`/beta。

---

## 平台与运行时

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10 或 Windows 11，x64 |
| Node（NPM 安装器） | `>=20` |
| Python（仅源码开发/构建） | 3.12 / 3.13 |
| 桌面安装器 | Windows x64，NSIS 安装版或 portable `.exe`，无需管理员权限 |
| 不支持 | Windows ARM64、Windows 7/8、macOS、Linux、WSL 内运行产品 |

消费级路径（`setup`/`serve`/`host`/BYOK 分析）不需要安装 Harness²；只有高级严格评测
CLI（`run`/`evaluate`/`promote`）需要 pinned 上游或 `MORPHD_HARNESS2_SRC`。

---

## 产品默认值

| 项 | 默认 |
|---|---|
| 信任等级 | 顾问（只建议，不自动改） |
| 变更幅度 | 保守（只改现有说明书；1 文件 / 30 行；不新增脚本） |
| 自动应用 | 关 |
| 通知 | 应用内；系统通知关；静默时段 22:00–08:00 |
| 每周主动建议 | 最多 2 条；其余进入待看 |
| 数据保留 | 30 天；遥测关 |
| 视觉 | 仅 `#76B900` + `#FFFFFF` + `#000000`；无灰/渐变/半透明；圆角 4/6/8/10px |

---

## 快速开始

### NPM（发布后）

```powershell
npx @harness2-morphd/cli@latest setup
npx @harness2-morphd/cli@latest doctor
npx @harness2-morphd/cli@latest uninstall
```

`setup` 完成宿主检测/适配，并打开三步引导 UI（宿主、模型/BYOK、预算与隐私）。
完成引导后按 Ctrl+C 关闭临时安装器 UI；安装器不会常驻。平台引擎通过
`@harness2-morphd/win32-x64` 经 `optionalDependencies` 分发，不使用任意 `postinstall`，
不从任意 URL 下载可执行文件。

### Electron（Windows 10/11 x64）

开发：

```powershell
npm --prefix consumer/electron install
npm --prefix consumer/electron run build
npm --prefix consumer/electron start -- --host dsh --launch-host
```

打包（未签名）：

```powershell
npm run release
```

产物位于 `release/electron/`，包含 NSIS 安装版与 portable `.exe`。打包前会把 Windows
引擎复制到 `consumer/electron/resources/engine/morphd-engine.exe`，应用不会在运行时下载引擎。

### 从源码运行（开发/自托管）

```powershell
py -3.12 -m venv .venv-win
.venv-win\Scripts\Activate.ps1
python -m pip install -e ".[dev,tray,build]"
npm --prefix consumer/web install
npm --prefix consumer/web run build

morphd setup --host dsh --project C:\path\to\project
morphd serve --config-only --open
```

本地 UI 地址形如 `http://127.0.0.1:<随机端口>/#bootstrap=<一次性令牌>`。令牌在 URL
fragment 中，不进入日志或查询参数；页面交换为 HttpOnly、SameSite=Strict 会话 cookie，
所有写请求再校验 CSRF。不得从局域网访问。

---

## 核心流程

```
宿主启动 / wrapper
  └─ harness2-morphd 本地服务（127.0.0.1，随机端口） + 任务运行器（并发 1）
       ├─ 会话结束 → 记录 SessionRecord（隐身/暂停除外）
       ├─ triage：零模型调用；无信号 → noop，$0
       ├─ 有信号 → 构造任务（脱敏 workspace + U1..Um）
       ├─ BYOK Proposer（工具模式 / JSON 补丁模式）
       ├─ 安全扫描（surface / 密钥 / 权限 / 预算 / 大小 / 路径）
       ├─ 候选 + 自然语言说明 + diff + 成本
       └─ 默认待看；应用前快照；应用后试用/自动回滚
宿主退出 → 写检查点、停止运行器、关闭端口、托盘消失
```

分析触发与展示解耦：静默时段、专注模式、每周上限、负反馈降频只影响**何时展示**，
不会在后台偷偷换渠道推送；所有被抑制建议都留在本地待看。

---

## 安全与隐私

- **Key**：优先 Windows Credential Manager（通过 `keyring`）；不可用时使用带用户口令的
  AES-GCM 本地保险库。Key 不写入配置、日志、导出、浏览器存储；界面只显示尾号。
- **本地 API**：仅 loopback；Host 头校验；随机端口；256-bit 引导令牌；`HttpOnly;
  SameSite=Strict` cookie；`X-Morphd-CSRF`；CSP；无 CORS；所有写操作校验状态机。
- **候选硬约束**：不得修改权限/沙箱/approval/hooks/settings/MCP、不得读取凭据、不得访问
  workspace 之外路径；Python AST + 密钥形状 + 大小/路径/surface 扫描；失败零写入。
- **快照与撤销**：应用前快照；撤销检测手动修改冲突；第一次快照长期保留用于“恢复原始 harness”。
- **预算**：月度 / 单会话 / 单次分析三层硬上限；未知价格不按 0 计费；达到阈值后在下一次
  调用前停止，绝不先花后通知。
- **数据**：默认不上传遥测、不上传会话原文；发送前可预览脱敏内容；可导出/删除；默认保留 30 天。
- **无暗黑模式**：无闪烁、数字红点堆积、倒计时或“错过优化”文案；关闭通知后不换渠道继续打扰。

---

## 引擎与高级入口

现有 Python 引擎继续作为本地库/高级入口使用，保留任务契约、三运行时回放、七指标、确定性
判分、配对统计、成本双闸、评测门禁、晋升/回滚、经验库隔离。消费级默认不再使用 cron/CI
调度；企业严格模式仍可使用 CLI 与双签。容器/egress 严格模式需要外部容器运行时（如
Docker Desktop），本仓库不再附带 Linux 容器镜像。

```powershell
morphd tasks import ...          # 从 dsh 会话构建任务库（企业/高级）
morphd run --dry-run ...         # 预检，零模型调用
morphd evaluate ... --gate       # 影子评测（严格模式）
morphd promote ... --p2 --p3     # 晋升（严格模式）
morphd rollback ...              # 快照回滚
morphd report / judge / parity   # 诊断与企业报表
morphd privacy export/delete     # 本地数据
```

**Codex 重要变更**：消费级与引擎默认不再使用
`--dangerously-bypass-approvals-and-sandbox` / hook bypass；改用
`--sandbox workspace-write --ask-for-approval never`。若某 Codex 版本无法提供可用的非
bypass 沙箱，应标记 beta/不支持，而不是降低安全标准。

---

## 仓库结构

```
PRD-v0.5.md
morphd/                    # Python 引擎 + consumer/
  consumer/                # 设置、BYOK、凭据库、宿主发现、队列、候选、API、托盘
  consumer/webui/          # 构建后的离线 Web UI 资源
  cli/                     # morphd CLI（高级/诊断）
  runtimes/                # dsh / codex / claude-code substrate + adapter
  base_harnesses/          # 三运行时基线树
  templates/ schemas/      # approver 模板与契约 schema
tests/                     # 引擎 + 消费层 pytest + 假 CLI
consumer/
  web/                     # React + Vite + TypeScript UI
  electron/                # Electron main/preload/tray/Windows 打包配置
packages/
  cli/                     # @harness2-morphd/cli（win32-x64）
  tray/                    # @harness2-morphd/tray（win32-x64）
  platform/win32-x64/      # @harness2-morphd/win32-x64 平台引擎包
scripts/                   # PowerShell 构建、打包、vendor 校验
docs/                      # 架构、运行、兼容、隐私、条款、验证、ADR、UI 证据
vendor/harness2-src/       # 未修改的 pinned 上游副本（Apache-2.0）
```

---

## 开发、测试与构建

```powershell
# 安装依赖
py -3.12 -m venv .venv-win
.venv-win\Scripts\Activate.ps1
python -m pip install -e ".[dev,tray,build]"
npm --prefix consumer/web install
npm --prefix packages/cli install
npm --prefix consumer/electron install --ignore-scripts

# 全量校验
npm test                       # ruff + pytest + web + CLI + tray + Electron 安全测试
npm run verify:vendor          # vendor SHA256 校验

# Web UI
npm --prefix consumer/web run lint
npm --prefix consumer/web run build

# Windows 引擎与安装包
npm run engine:build           # PyInstaller -> dist/morphd-engine.exe
npm run package:win            # -> packages/platform/win32-x64/bin + SHA256SUMS
npm --prefix consumer/electron run dist -- --win --x64

# 一次性生成 npm tarball、Electron 安装包和校验和
npm run release
```

假 CLI 覆盖三运行时单轮/多轮、usage、产物、判分、审批、预算、评测门禁、晋升/回滚、报告；
消费层覆盖设置/策略、凭据保险库、BYOK 客户端/提案器、宿主适配/回滚、dsh 会话导入、
100 会话幂等入队、隐身零捕获、候选应用/撤销、API 鉴权/CSRF/CSP、手动分析成本预览与重置学习。

真实 CLI/模型、签名与安装器真机验收情况见
[`docs/verification.md`](docs/verification.md) 与 PRD 附录 C.7 的 `n/a` 规则。

---

## 兼容矩阵（基线）

| 组件 | 版本 |
|---|---|
| Harness² | `1cfe254e8d25`（未修改） |
| dsh | `0.2.1-rc.2` |
| Claude Code | `2.1.302` |
| Codex | `0.144.6` |
| Python | 3.12 / 3.13 |
| Node（安装器） | `>=20`；dsh 自身 `^22.19.0` 或 `>=24.0.0` |
| 平台 | Windows 10/11 x64 |

dsh/Claude/Codex 的**原生**插件/hook API 仍需真机验证；当前安装器提供 wrapper 与可用的
Claude `SessionEnd` hook。没有原生 hook 时，会话结束分析在下次宿主启动时从队列续跑，
不伪造后台常驻。

---

## 已知限制

- Windows 引擎与 Electron 安装器**未做 Authenticode 签名**；SmartScreen 可能提示未知发布者。
- 未提供 macOS/Linux 平台包、安装器或系统托盘支持。
- 真实 dsh/Claude/Codex 生命周期、Codex 事件/续跑、Claude `stream-json` 完整 schema 仍需
  在目标机器验收；以 `n/a`/beta 标注，不用 0 或空轨迹替代。
- NPM 托盘 helper 依赖可选 `pystray`/`Pillow`；缺失时明确降级为浏览器 UI。
- 自动更新未实现；升级通过重新安装 npm 包或下载新版安装器完成。
- 企业严格模式的容器/egress 验证需要外部容器环境；消费级依赖宿主原生沙箱。

---

## License 与品牌

Apache-2.0。vendored Harness² 保留原始版权与许可，见 `NOTICE` 与
`vendor/harness2-src/PROVENANCE.md`。视觉仅使用 NVIDIA 绿色值 `#76B900`，不使用 NVIDIA
Logo、商标或暗示官方合作。
