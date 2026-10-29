# 角色与证据契约

| 角色 | 模型 / 推理 | 输入 | 可做什么 | 输出 |
| --- | --- | --- | --- | --- |
| 项目经理 manager | gpt-6.1-sol / high | issue、基线 | 定范围、计划与验收 | ready 或 blocked |
| 分析员 analyst | gpt-6.1-sol / high | 契约、源码、失败日志 | 定位与建议 | 原因及最小改法 |
| 协作顾问 adviser | gpt-6.1-sol / high | 分析与契约 | 独立审阅负向条件 | 范围和回归约束 |
| 开发员 developer | gpt-6.1-sol / high | 已固定验收、失败证据 | 仅改允许的策略源码 | 文件清单和修复说明 |
| 验证员 verifier | gpt-6.1-sol / high | 当前源码、实际测试结果 | 只读独立审查 | passed / failed |
| 发布员 publisher | gpt-6.1-sol / high | commit、验证摘要、具体发布计划 | 审查本地服务与可选草稿 PR 条件 | passed / blocked |
| 维护专员 maintainer | gpt-6.1-sol / high | HTTP 实测、清单和 PR 版本回执 | 审查发布后行为 | 观察结论与限制 |
| 状态管理员 administrator | gpt-6-luna / max | 观察、修复和版本证据 | 复核归档条件 | 结果摘要与经验候选 |

默认模型来源与推断边界在 `models.json`，配置为本次快照值，不自动追随网页改变。修改后重启服务。

## 输出契约

每次 Codex 会话输出 `summary`、`decision`、`findings`、`changed_files`、`limitations`。外层 receipt 由控制器补充真实 `thread_id`、角色、模型、推理强度、退出码、耗时和 usage，不让模型填写这些事实。

角色回复只是审阅结论。真正的开发候选、测试通过、发布完成都必须由 Git、编译器、测试进程和 HTTP 结果支持。失败、超时、缺失证据都不能标记成功。

## 权限与失败

所有 Codex 会话只读。开发会话返回 `replacement.path`、`replacement.base_sha256` 和 `replacement.content`；控制器只允许写入单独 Git 克隆的 `src/BatteryPolicy.kt`，并核对基线摘要。签名、验收、probe、来源说明和仓库指令固定。控制器拥有源码应用、Git 提交、可信测试、故障注入、本地发布等权力。

开发失败或验证不通过时，新的开发会话接收具体日志；不复用上一会话的隐含状态。达到两轮修复上限即阻塞。角色不能静默更换模型、放宽验收、自行推送分支、关闭 issue 或把本例称作真机修复。

GitHub 目标由控制器从工单关联仓库的 Git 元数据发现。发布员模型只审查已授权的计划；控制器使用 Git/gh 创建新分支与草稿 PR，核对实际 head SHA，并记录可复核的 URL。模型回复不能作为“PR 已发布”的证明。PR 创建不等于 CI 通过或合并。

默认故障注入明确记录为测试设施行为。报告同时保留注入前的真实模型候选测试结果、注入后的失败、修复后的通过，避免把故意注入的回归冒充模型自然失误。
