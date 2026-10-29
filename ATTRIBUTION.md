# 模仿源头与致谢 / Attribution

## 主要来源：Codex Radar

本项目明确以 **Codex 雷达站（Codex Radar）** 作为界面与工作流程的模仿、学习及灵感来源：

- 首页：https://codexradar.com/
- 开发直播：https://codexradar.com/dev-live/
- 角色模型配置参考的公开快照：https://codexradar.com/api/development-live/current

参考内容包括公开可见的“发现、分析、开发、验证、发布、反馈”循环、常驻角色与执行工单的区分、工单进度展示，以及黑绿配色的工作台风格。感谢原站作者将这种工作流程公开展示。

`models.json` 保留 2026-10-29 的配置观察时间及来源。常驻角色采用当时公开快照；执行角色的默认映射是本项目据公开工单作出的选择，并非原站已公开的完整角色配置。

## 独立实现与关系说明

本仓库的 Python/SQLite 状态机、Codex CLI 编排、固定验收、Git 隔离、多仓库登记及 PR 发布逻辑由本项目自行实现。公开网页无法证明原站后台采用了相同实现，本项目也未获得或包含原站的后台源码。

这是独立社区项目，不是 Codex Radar 或 OpenAI 的官方项目、分支或受其背书的产品。来源链接用于致谢和说明参考范围。Codex Radar、Codex、OpenAI 等名称与商标归各自权利人所有。

未将原站页面、截图、图标或其他媒体打包进本仓库；外部页面也不适用本仓库的 MIT 许可证。

## 运行时依赖

本项目调用用户自行安装、登录的 Codex CLI、Git、GitHub CLI 和 JDK。Kotlin/JVM 编译依赖由安装脚本按 `toolchain-lock.json` 校验后缓存，依赖二进制不随本仓库分发，各自遵循上游许可证。

## 教学样例

`examples/permission-policy/` 提供独立的、简化的权限策略验收场景，用于演示失败检测与自动修复。它不携带业务应用源文件、私有仓库链接或设备记录，也不是针对某个真实产品的修复发布。
