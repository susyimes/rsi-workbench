# 安装和运行

## 1. 打开仓库

先克隆公开仓库，再用 Codex 的“添加项目”选择仓库根目录：

```powershell
git clone https://github.com/susyimes/rsi-workbench.git
cd ./rsi-workbench
```

让 Codex 先读 `AGENTS.md`、本文件和 `README.md`。仓库不携带账号、令牌或机器专属路径。

## 2. 前置环境

- Python 3.10+ 和 Git。
- 已安装 Codex CLI，并执行过 `codex login`。运行使用本机现有登录，不复制账号配置。
- JDK 17+；将 `JAVA_HOME` 指向 JDK。Windows 上建议使用已安装的 Android Studio JBR。
- Kotlin 2.0.21 的固定编译依赖。优先复用本机 Gradle 缓存；缺少时显式下载。
- 当前账号能够使用 `models.json` 中的模型。不可用时阻塞并显示真实错误，不静默换模型。

```powershell
python install.py
# 只有缺少固定依赖时才需要下载：
python install.py --download
```

Windows npm 安装的 Codex 原生可执行文件会自动定位；特殊安装路径可设置 `RSI_CODEX_EXE` 指向真正的可执行文件，不支持 `.cmd/.bat/.ps1` 包装器。不需要 Python 第三方包，Node 仅用于前端语法检查。

## 3. 运行真实闭环

```powershell
python server.py --run-example --auto-release-local
```

浏览器打开 http://127.0.0.1:8765/ 。`--run-example` 幂等创建并启动 权限策略 工单；`--auto-release-local` 明确预授权验证通过的同一版本发布到本机测试服务，不包含任何远程部署。

没有此授权参数时，流程停在“待发布确认”，打开工作台的“发布…”核对目标后继续。正常路径是：

1. 经理、分析员、协作顾问分别建立 Codex 会话，分析真实失败基线。
2. 开发员返回唯一允许的策略文件完整内容和原始摘要；控制器校验后写入独立 Git 克隆。
3. 控制器运行固定 20 项 Kotlin 验收。首个模型候选通过后，默认注入一次显式回归以测试修复回路。
4. 真实编译/测试和独立验证员发现失败，失败日志交给新的开发会话修复；最多两轮。
5. 验证与授权绑定同一候选摘要。发布员审查后，控制器复制已验证的 JAR，不重新编译。
6. 本地 HTTP 服务执行该 JAR；维护员检查实际 HTTP 探针，状态管理员复核完成证据。

`--no-fault-injection` 关闭测试用注入。真实模型调用会消耗当前 Codex 账号额度；每次角色会话默认超时 420 秒。

## 4. 数据与恢复

默认数据：`runtime/codex/`。使用 `--data <目录>` 指定其他位置；安装检查使用相同的 `--data`。此目录包含 SQLite、隔离 Git 仓库、模型原始事件、测试日志和发布产物，不进入 Git。

Ctrl+C 停止服务及其本地发布服务。再次启动同一数据目录，会恢复已完成本地发布的相同端口与相同字节；端口被其他进程占用时启动失败，不接管其他服务。未完成执行等待租约过期后恢复，最多两次；旧结果不得回写。

“停止”取消当前执行与状态推进，保留证据；阶段输出使用独立目录。若刚完成发布，可通过停止工作台关闭其拥有的本地服务。失败阶段与旧证据永久保留，手动“重新开始”会从分析阶段建立新一轮有界执行。

基础设施异常修好后，也可以通过 `POST /api/tasks/{id}/resume` 重试原失败阶段；它保留修复计数，不能跳过测试失败的修复上限。

补齐证据后，`POST /api/tasks/{id}/reverify` 清除旧验证和授权，重新验证同一候选。写接口需要 `X-RSI-Client: local-dashboard` 和 JSON 请求。

## 5. 验证和演示模式

```powershell
python -B -m unittest discover -s tests -v
node --check web/app.js
python server.py --mode demo --seed --port 8767
```

单元测试使用显式测试替身，不消耗模型额度；它们不是实跑凭据。真实证据必须有 Codex session ID、模型配置、实际命令退出码和文件摘要。演示数据库不允许切换为真实数据库。

工单完成后导出证据（不复制机器绝对路径、原始 JSONL 或登录信息）：

```powershell
python export_run.py --data runtime/codex --task 1 --output reports/my-run
```

## 6. 添加多个仓库与发布 PR

1. 打开“仓库管理 → 添加仓库”，输入已有本地 Git 工作目录，显示名称可选。可添加多个；相同工作目录或其子目录自动去重。登记只读取 Git 信息。
2. 页面自动识别 GitHub remote 和目标分支。优先使用当前分支上游的 remote，其次 origin；没有明确优先项且存在多个 GitHub 仓库时拒绝猜测。目标分支优先使用 `branch.<当前分支>.gh-merge-base`，其次常用开发分支的 Git 上游，再使用 remote HEAD。必要时只读查询远程默认分支。
3. 从仓库卡片新建工单，或在工单表单选择仓库。支持的策略仓库必须干净，执行时另建独立克隆；源工作目录保留原样。其他类型显示“需要接入本项目的验收适配器”。
4. 需要 GitHub PR 时，先在本机安装 GitHub CLI 并完成 `gh auth login`，确认 Git 对已识别 remote 具有推送权限。本工具复用现有登录，不存储令牌，也不自动登录。
5. 验证通过后点击“发布…”，页面重新识别关联仓库并显示具体仓库、目标分支和验证版本。“同时创建 GitHub 草稿 PR”默认在目标可用时勾选；确认后才进入该发布步骤。无 remote 的内置样例只能本地发布。
6. 发布器核对目标分支仍属于候选历史、改动只包含策略源码，然后将验证提交推到新 `rsi/task-<工单>-<commit前12位>` 分支，创建或复用草稿 PR。页面显示 PR 地址及本地服务地址；草稿待审查和 CI 未查询状态单独记录。

`--auto-release-local` 永远只授权本地服务。已完成工单也可打开“发布详情”对同一验证版本补充发布。目标或候选改变时旧预览失效，需要重新查看；远程 base 领先于候选时会阻塞，必须基于最新目标重新开发验证。

PR 创建使用确定分支和身份标记。超时后先查询结果，重试不会盲目重复创建。远程动作无法和 SQLite 原子提交：停止不会撤回 GitHub 已接受的请求，也不会删除已推送分支。发布意图和回执保存在 `execution/task-<id>/publications/`；执行异常可在查看证据后通过原阶段 `resume` 恢复核对。不会自动合并、发 issue 评论或部署生产环境。

## 7. 扩展到业务仓库

登记业务仓库后，为它增加经过审查的项目适配器，固定可写文件、可信验收和隔离策略。Android 接入还需要 variant Gradle 构建、设备标识、设备验收和发布策略；本例没有实施这些环节。多仓库登记不等于任意业务项目已经可以自动开发。

参考：[Codex 非交互执行](https://learn.chatgpt.com/docs/non-interactive-mode)、[公开开发直播](https://codexradar.com/dev-live/)。
