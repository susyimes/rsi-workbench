# 贡献指南

欢迎提交问题或 Pull Request。开始修改前请阅读 AGENTS.md、Install.md 和 ROLE_CONTRACTS.md。

1. Fork 仓库并在独立分支开发。保持修改聚焦，描述问题、实际行为和验证结果。
2. 控制服务使用 Python 标准库；不要把模型回复作为测试通过的证据。
3. 核心流程改动运行 `python -B -m unittest discover -s tests -v`；前端改动运行 `node --check web/app.js`。
4. 不要为了通过候选而放宽固定验收。新增业务适配器必须明确可写文件、隔离策略、可信测试与发布目标。
5. 请保留 README、ATTRIBUTION.md 与界面中的来源说明。不要提交账号、令牌、真实运行数据库、个人会话日志或业务私有内容。

提交可公开复现的日志片段，并区分测试替身、真实模型调用和设备验证。真实 Codex 集成消耗执行者账号额度，不应作为默认单元测试运行。
