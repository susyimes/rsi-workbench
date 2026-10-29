# 验证范围

2026-10-29，公开源代码目录实测：**56 项自动测试全部通过**；`node --check web/app.js` 通过。下述测试替身与真实集成范围分别说明。

公开版本应在本地运行以下检查：

```powershell
python -B -m unittest discover -s tests -v
node --check web/app.js
```

自动测试覆盖状态机、事务幂等、租约和停止、修复上限、项目互斥、模式隔离、源码范围、固定验收、产物摘要、多仓库登记与持久化、Git 目标发现、发布计划绑定和 PR 幂等恢复。

Git 发布测试使用真实本地 bare 仓库执行 fetch/push/ls-remote；GitHub API 与模型调用使用明确的测试替身。测试不会登录 GitHub、创建远程 PR 或消耗 Codex 模型额度。

真实闭环需要按 Install.md 运行：独立 Codex 会话、实际 Kotlin 编译与 20 项固定验收、失败修复、同一产物的本机发布，以及 5 项 HTTP 行为探针。是否成功以当前运行的退出码、会话回执、摘要及探针结果为准。运行记录位于私有 runtime 目录，公开仓库不预置个人会话日志。

GitHub 草稿 PR 是否创建以实际回读的 URL、仓库、base 与 head SHA 为准；本地测试不代表 GitHub CI 通过。Android 构建、APK 发布和真机验收不属于当前内置适配器的验证范围。
