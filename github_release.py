"""Publish the already-verified commit as a draft PR; never merge or rewrite a branch."""
import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlencode

from repositories import discover, github_name
from toolchain import command, write_json


class PublicationError(RuntimeError):
    pass


def pr_body(job,candidate,verified,identity):
    checks=verified['checks']
    return f'''This change applies the verified RSI policy candidate for work item {job['id']}.

Validation: compilation exited {checks['compile']['exit_code']}; {checks['passed_count']}/{checks['total']} acceptance checks passed; independent Codex review passed.

- Verified commit: `{candidate['commit']}`
- Candidate digest: `{candidate['artifact_digest']}`
- Verified JAR SHA-256: `{checks['jar_sha256']}`
- Repair rounds: {job['repair_count']}

Scope: isolated Kotlin/JVM policy fixture. Android/device validation and GitHub CI have not been performed by this publication step. This draft is for review; no merge or production deployment is requested.

<!-- rsi-publication:{identity} -->
'''


def validate_pr(value,target,branch,commit,marker):
    name=target['github_repository']
    number=value.get('number')
    if (type(number) is not int or number<1 or value.get('html_url','').lower()!=f'https://github.com/{name}/pull/{number}'.lower()
        or value.get('state')!='open' or value.get('draft') is not True
        or value.get('head',{}).get('sha')!=commit or value.get('head',{}).get('ref')!=branch
        or value.get('base',{}).get('ref')!=target['base_branch']
        or value.get('base',{}).get('repo',{}).get('full_name','').lower()!=name.lower()
        or value.get('head',{}).get('repo',{}).get('full_name','').lower()!=name.lower()
        or marker not in (value.get('body') or '')):
        raise PublicationError('PR 的版本、目标、草稿状态或归属标记不匹配；已停止')
    return {'status':'published','url':value['html_url'],'number':number,'repository':name,
            'base':target['base_branch'],'head':branch,'head_sha':commit,'draft':True,
            'review_status':'awaiting-review','ci_status':'not-checked','base_sha':value['base'].get('sha')}


class GitHubPublisher:
    def __init__(self, heartbeat, executor=None):
        self.heartbeat=heartbeat;self.executor=executor;self.sequence=0

    def run(self,argv,repo,folder,timeout=90):
        self.sequence+=1
        if not self.heartbeat():
            raise PublicationError('发布已取消或租约已失效')
        if self.executor:
            return self.executor(argv,repo)
        log=folder/f'command-{self.sequence}.log'
        result=command(argv,repo,log,self.heartbeat,timeout,env={
            'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never','GH_PROMPT_DISABLED':'1','NO_COLOR':'1'})
        if result['exit_code']:
            raise PublicationError(f'发布命令失败（退出码 {result["exit_code"]}），详情保留在本地发布日志')
        return log.read_text(encoding='utf-8',errors='replace').strip()

    def api(self,endpoint,repo,folder):
        return json.loads(self.run(['gh','api','--hostname','github.com',endpoint],repo,folder))

    def find(self,target,branch,repo,folder):
        query=urlencode({'state':'all','head':target['github_repository'].split('/')[0]+':'+branch,
                         'base':target['base_branch'],'per_page':100})
        result=self.api(f'repos/{target["github_repository"]}/pulls?{query}',repo,folder)
        if not isinstance(result,list) or len(result)>1:
            raise PublicationError('同一发布分支存在不明确的 PR 记录')
        return result[0] if result else None

    def publish(self,job,candidate,verified,plan,folder):
        target=plan.get('github_pr')
        if not target:
            return {'status':'not-requested'}
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        if not self.executor and not shutil.which('gh'):
            raise PublicationError('需要 GitHub CLI（gh）和已有登录')
        repo=Path(candidate['repo']);revision=candidate['commit']
        if not re.fullmatch('[0-9a-f]{40,64}',revision):
            raise PublicationError('候选 Git revision 无效')
        if (candidate['artifact_digest']!=job['approved_digest'] or candidate['artifact_digest']!=plan['artifact_digest']
            or verified.get('passed') is not True or verified.get('commit')!=revision
            or verified.get('artifact_digest')!=candidate['artifact_digest']):
            raise PublicationError('PR 必须使用已验证且已授权的同一候选')
        # Resolve from the actual candidate's Git chain, not an issue URL or a model response.
        discovered=discover(repo)
        if ((discovered.get('github_repository') or '').lower()!=target['github_repository'].lower()
            or discovered.get('base_branch')!=target['base_branch']
            or discovered.get('remote_url')!=target['remote_url']):
            raise PublicationError('候选仓库的 Git remote/目标分支与发布授权不同，请重新发现并授权')
        if self.run(['git','status','--porcelain'],repo,folder):
            raise PublicationError('候选仓库存在未提交修改')
        if self.run(['git','rev-parse','HEAD'],repo,folder)!=revision:
            raise PublicationError('候选 HEAD 已改变')
        name=target['github_repository'];base=target['base_branch'];remote=target['remote_url']
        if github_name(remote)!=name:
            raise PublicationError('发布 remote 不是已识别的 GitHub 仓库')
        branch=f'rsi/task-{job["id"]}-{revision[:12]}'
        self.run(['git','check-ref-format','refs/heads/'+branch],repo,folder)
        identity=hashlib.sha256(json.dumps([name,base,branch,revision,candidate['artifact_digest']],separators=(',',':')).encode()).hexdigest()
        marker=f'<!-- rsi-publication:{identity} -->'
        # Fetch only the base into an internal ref; no checkout/reset/rebase and no write to the source branch.
        base_ref='refs/rsi/pr-base/'+identity[:16]
        self.run(['git','fetch','--no-tags',remote,f'refs/heads/{base}:{base_ref}'],repo,folder)
        self.run(['git','merge-base','--is-ancestor',base_ref,revision],repo,folder)
        changed=self.run(['git','diff','--name-only',base_ref,revision],repo,folder).splitlines()
        if not changed or set(changed)-{'src/BatteryPolicy.kt'}:
            raise PublicationError('PR 变更不符合当前策略样例的文件范围；请重新基于目标分支开发验证')
        write_json(folder/'intent.json',{'repository':name,'base':base,'head':branch,'commit':revision,
            'candidate_digest':candidate['artifact_digest'],'identity':identity,'draft':True})
        existing=self.find(target,branch,repo,folder)
        if not existing:
            refs=self.run(['git','ls-remote',remote,'refs/heads/'+branch],repo,folder)
            if refs:
                if refs.split()[0]!=revision:
                    raise PublicationError('远程分支已存在且版本不同，不会覆盖')
            else:
                # Empty expected ref makes this a create-only push, even under a race. Never overwrite an existing branch.
                self.run(['git','push',f'--force-with-lease=refs/heads/{branch}:',remote,
                          f'{revision}:refs/heads/{branch}'],repo,folder)
            refs=self.run(['git','ls-remote',remote,'refs/heads/'+branch],repo,folder)
            if not refs or refs.split()[0]!=revision:
                raise PublicationError('远程分支 SHA 未确认')
            body_path=folder/'pr-body.md'
            body_path.write_text(pr_body(job,candidate,verified,identity),encoding='utf-8',newline='\n')
            title='test(rsi): '+str(job['title']).replace('\n',' ').replace('\r',' ')[:100]
            creation_error=None
            try:
                self.run(['gh','pr','create','--repo',name,'--base',base,'--head',branch,
                    '--draft','--no-maintainer-edit','--title',title,'--body-file',str(body_path)],repo,folder)
            except (PublicationError,TimeoutError) as error:
                creation_error=error
            # An ambiguous create is recovered by its deterministic branch; never blindly create another PR.
            existing=self.find(target,branch,repo,folder)
            if not existing:
                raise creation_error or PublicationError('GitHub 未返回可核验的 PR')
        result=validate_pr(existing,target,branch,revision,marker)|{'identity':identity}
        write_json(folder/'receipt.json',result)
        return result

    def observe(self,receipt,repo,folder):
        target={'github_repository':receipt['repository'],'base_branch':receipt['base']}
        value=self.api(f'repos/{receipt["repository"]}/pulls/{receipt["number"]}',repo,folder)
        return validate_pr(value,target,receipt['head'],receipt['head_sha'],f'<!-- rsi-publication:{receipt["identity"]} -->')
