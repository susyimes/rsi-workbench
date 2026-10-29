# RSI Workbench · 自迭代工作台

通过真实 Codex 会话，串起多角色协作、开发、验证、失败修复和发布的本地工作台。

**模仿与灵感来源：[Codex 雷达站（Codex Radar）](https://codexradar.com/) 和它的[开发直播页面](https://codexradar.com/dev-live/)。** 本项目参考其公开可见的 RSI 六步循环、常驻角色和状态看板，自行实现调度与执行系统。感谢原站提供的展示思路。详细说明见 [ATTRIBUTION.md](ATTRIBUTION.md)。

这是独立的社区实现，与 Codex Radar、OpenAI 均无官方隶属关系；本仓库不包含原站后台源码，不声称复现其未公开实现。

An independent, local multi-role development workbench inspired by Codex Radar. Python standard library, SQLite, real Codex CLI sessions, and a fixed Kotlin/JVM acceptance fixture.

## 已实现

- 六步闭环：发现 → 分析 → 开发 → 验证 → 发布 → 反馈。
- 经理、分析员、顾问、开发员、验证员、发布员、维护员和状态管理员使用独立 Codex 会话。
- 多个本地 Git 仓库登记，自动发现 GitHub remote 和目标分支，工单绑定仓库。
- 独立 Git 克隆、固定验收、最多两轮自动修复、租约与项目互斥。
- 验证与发布绑定同一提交和产物；本机测试服务与可选 GitHub 草稿 PR 分别展示。
- 失败证据留存、停止与恢复、幂等发布、快照看板。

## 快速开始

需要 Python 3.10+、Git、已登录的 Codex CLI，以及真实策略样例所需的 JDK 17+。Kotlin 编译依赖优先复用本机 Gradle 缓存；GitHub PR 另需已登录的 GitHub CLI。

```powershell
git clone https://github.com/susyimes/rsi-workbench.git
cd rsi-workbench
python install.py
# 如果缺少固定编译依赖：python install.py --download
python server.py --run-example --auto-release-local
```

打开 http://127.0.0.1:8765/ 。`--port 8766` 可指定其他端口。`--auto-release-local` 只预授权本机测试发布，不会自动推送 GitHub PR。真实角色调用会消耗当前 Codex 账号额度。

仅查看界面和状态机，可以运行不调用模型的演示模式：

```powershell
python server.py --mode demo --seed --port 8767
```

完整安装、恢复与发布流程见 [Install.md](Install.md)。

## 在 Codex 中打开

把本仓库添加为 Codex 项目，然后输入：

> 请读取 AGENTS.md 和 Install.md，检查依赖，按照 models.json 的角色配置，在独立测试仓库运行权限策略样例。允许把通过验证的同一版本发布到本机测试服务，完成后核对证据和验收结果。

`AGENTS.md` 是自动发现的仓库指令，`agent.md` 是兼容入口。`Install.md` 提供安装步骤；不会仅因文件存在就自动执行安装。

## 多仓库与 PR 发布

1. 在“仓库管理 → 添加仓库”输入已有本地 Git 工作目录。支持多个仓库，相同根目录自动去重。
2. 新建工单时选择关联仓库。系统读取实际 Git remote、上游和默认分支，不把 issue URL 当作发布目标。
3. 验证通过后打开“发布…”，查看具体目标与提交，再选择“同时创建 GitHub 草稿 PR”。
4. 发布器只推送验证提交到新的 RSI 分支，并回读 PR 的仓库、base、head SHA 和草稿状态。重试复用同一 PR，不合并或覆盖已有分支。

没有 GitHub remote 时仍可登记仓库和运行本机样例，但不会伪造 PR 地址。添加入口接受本地路径，不会自动克隆任意远程业务项目。

## 当前执行范围

内置 `permission-policy-lab` 是独立 Kotlin/JVM 教学样例，演示查看权限与主动授权的不同策略。固定 20 项验收覆盖首次授权、OEM 负向证据、未知系统状态和兼容路径；默认在首个通过候选中注入一次明确记录的回归，随后由真实测试发现、由新的开发会话修复。

当前执行适配器只支持该样例及采用相同固定契约的仓库。其他仓库可以登记，但需要接入项目对应的开发、可写文件和验收适配器。完成样例不代表 Android 应用或真机已经验证。

公开仓库包含通用源代码、样例和测试；本机运行数据库、业务来源映射、个人会话日志与历史工作目录不随仓库发布。

## 配置与设计

| 文件 | 用途 |
| --- | --- |
| `models.json` | 角色模型、推理强度及公开配置来源 |
| `config.json` | 并发槽、租约、修复上限与常驻职责 |
| `prompts/` | 各角色的输入、输出与职责边界 |
| `AGENTS.md` / `Install.md` | Codex 协作约定与安装运行步骤 |
| `examples/permission-policy/` | 独立策略样例与固定验收 |
| `toolchain-lock.json` | 固定编译依赖的版本与 SHA-256 |

模型默认值记录的是 2026-10-29 观察到的公开配置，不保证每个账号都能访问相同模型；不可用时会报错，不静默替换。修改 `models.json` 后重启。

进一步阅读：[架构](ARCHITECTURE.md) · [角色契约](ROLE_CONTRACTS.md) · [验证范围](VALIDATION.md) · [贡献指南](CONTRIBUTING.md) · [来源说明](ATTRIBUTION.md)。

## 许可证

本项目自行实现的代码与文档采用 [MIT License](LICENSE)。外部网站、名称、商标及运行时第三方依赖不因本仓库的许可证而重新授权；具体来源和边界见 [ATTRIBUTION.md](ATTRIBUTION.md)。
