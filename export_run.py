"""Export reviewable evidence without credentials, raw CLI events, or machine paths."""
import argparse
import json
import shutil
from pathlib import Path

from engine import BASE, CONFIG, Store
from toolchain import sha, write_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--task',type=int,default=1)
    parser.add_argument('--output',type=Path,default=BASE/'reports/permission-policy')
    args=parser.parse_args();data=args.data.resolve();out=args.output.resolve()
    if not (data/'rsi.sqlite3').is_file():
        raise RuntimeError('Existing real execution database required')
    store=Store(data/'rsi.sqlite3',CONFIG|{'mode':'codex'})
    detail=store.detail(args.task)
    if detail['task']['state']!='done':
        raise RuntimeError('The task is not done; do not export an acceptance report')
    out.mkdir(parents=True,exist_ok=True)
    def clean(value):
        if isinstance(value,dict):
            return {k:clean(v) for k,v in value.items()}
        if isinstance(value,list):
            return [clean(v) for v in value]
        if isinstance(value,str):
            return value.replace(str(data),'<RUNTIME>').replace(str(BASE),'<REPO>')
        return value
    write_json(out/'workflow.json',clean(detail))
    runs=detail['runs']
    sessions=[]
    for path in sorted((data/'execution'/f'task-{args.task}').glob('run-*/*/receipt.json'),
                       key=lambda p:(int(p.parent.parent.name.split('-')[1]),p.parent.name)):
        receipt=json.loads(path.read_text(encoding='utf-8'))
        sessions.append({k:receipt[k] for k in ('role','model','effort','thread_id','exit_code','duration_seconds','usage')}
            |{'run_id':int(path.parent.parent.name.split('-')[1]),'decision':receipt['response']['decision'],
              'summary':receipt['response']['summary']})
    write_json(out/'sessions.json',sessions)
    successful=[r for r in runs if r['output'] and not r['output'].get('error')]
    analysis_run=[r for r in successful if r['stage']=='analyzing'][-1]
    analysis=analysis_run['output']
    successful=[r for r in successful if r['id']>analysis_run['id']]
    candidates=[r['output'] for r in successful if r['stage']=='developing']
    validations=[r['output'] for r in successful if r['stage']=='verifying']
    publication=[r['output'] for r in successful if r['stage']=='releasing'][-1]
    observation=[r['output'] for r in successful if r['stage']=='observing'][-1]
    for name,checks in [('baseline',analysis['baseline']),('before-injection',candidates[0]['pre_injection_checks'])]:
        (out/f'{name}.log').write_text(checks.get('test_output','compile failure'),encoding='utf-8',newline='\n')
    for i,validation in enumerate(validations,1):
        (out/f'verification-{i}.log').write_text(validation['checks'].get('test_output','compile failure'),encoding='utf-8',newline='\n')
    final=Path(candidates[-1]['repo'])
    shutil.copy2(final/'src/BatteryPolicy.kt',out/'BatteryPolicy.kt')
    (out/'candidate.diff').write_text((final.parent/'candidate.diff').read_text(encoding='utf-8'),encoding='utf-8',newline='\n')
    manifest=publication['release']['manifest']
    write_json(out/'release-manifest.json',manifest)
    pull_request=publication['release'].get('pull_request',{'status':'not-requested'})
    write_json(out/'pull-request.json',pull_request)
    pr_summary=(f'已创建草稿 PR：[{pull_request["repository"]}#{pull_request["number"]}]({pull_request["url"]})；'
        f'head `{pull_request["head_sha"]}`。创建时为待审查，CI 未查询。'
        if pull_request.get('status')=='published' else '未创建 GitHub PR；本次仅发布本机测试服务。')
    write_json(out/'http-probes.json',{'health':observation['health'],'probes':observation['probes']})
    rows=['| 执行 | 角色 | 模型 / 推理 | 结果 | 会话 ID |','| --- | --- | --- | --- | --- |']
    rows += [f'| {s["run_id"]} | {s["role"]} | {s["model"]} / {s["effort"]} | {s["decision"]} | `{s["thread_id"]}` |' for s in sessions]
    errors=[f'- 执行 {r["id"]} / {r["stage"]}：{r["output"].get("message",r["output"]["error"])}'
            for r in runs if r['output'] and r['output'].get('error')]
    report=f'''# 真实 Codex 集成验收

工单：独立权限策略样例。工单已完成；本报告仅证明隔离 Kotlin/JVM 样例的开发、验证、修复与本地发布闭环。

## 实际结果

- 基线：{analysis['baseline']['passed_count']}/{analysis['baseline']['total']} 通过，实际进程非零退出。
- 首个 Codex 候选：{candidates[0]['pre_injection_checks']['passed_count']}/20 通过。
- 显式故障注入：{any(c.get('fault_injected') for c in candidates)}；首次独立验证 {validations[0]['checks']['passed_count']}/20 通过，退回修复。
- 修复轮数：{detail['task']['repair_count']}；最终固定验收 {validations[-1]['checks']['passed_count']}/20 通过。
- 本地发布后 HTTP 行为探针：{sum(p['passed'] for p in observation['probes'])}/{len(observation['probes'])} 通过。
- 真实 Codex 会话：{len(sessions)}；其中角色返回 blocked 的会话 {sum(s['decision']=='blocked' for s in sessions)} 个；当前一轮验证阶段实际执行 {len(validations)} 次。

## 版本证明

- 最终测试仓库提交：`{manifest['commit']}`
- 候选身份：`{manifest['artifact_digest']}`
- 验证/发布的同一 JAR：`{manifest['files']['candidate.jar']}`
- 发布标签：`{publication['release']['tag']}`
- 本次本地健康地址：{publication['release']['url']}/health（工作台运行时可用）
- GitHub：{pr_summary}

编译证明将源码提交、构建输入摘要、受保护文件与 JAR 摘要绑定，见 workflow.json 最后一次验证的 input_attestation。发布阶段复制该 JAR，不重编译。

## 集成中真实遇到的阻塞

{chr(10).join(errors) if errors else '本次运行没有阶段异常阻塞。'}

这些失败保留在 workflow.json 和 sessions.json 中。注入回归是显式测试设施行为，不冒充模型自然失误。

## 会话

{chr(10).join(rows)}

## 范围限制

没有修改源工作仓库、合并业务分支或关闭 issue，没有发布 APK，也没有真实设备验证。GitHub 分支与草稿 PR 的实际结果以上述回执为准。报告中的策略是独立教学样例，不是完整 Android 复现。原始 JSONL、完整提示和机器路径保留在私有运行目录，不进入交付 Git 仓库。
'''
    (out/'REPORT.md').write_text(report,encoding='utf-8',newline='\n')
    write_json(out/'export-manifest.json',{p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file() and p.name!='export-manifest.json'})
    print(json.dumps({'report':str(out/'REPORT.md'),'sessions':len(sessions),'commit':manifest['commit'],
                      'jar_sha256':manifest['files']['candidate.jar']},ensure_ascii=False))


if __name__=='__main__':
    main()
