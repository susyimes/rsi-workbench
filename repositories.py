"""Repository registry and bounded discovery from actual Git metadata."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


def git(path,*args,allow_failure=False):
    try:
        result=subprocess.run(['git','-C',str(path),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,
            env=os.environ|{'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never'})
    except (OSError,subprocess.TimeoutExpired) as error:
        raise ValueError('Git 信息读取失败或超时，请检查路径、网络与现有登录') from error
    if result.returncode and not allow_failure:
        raise ValueError('无法读取 Git 仓库：'+result.stderr.strip()[:250])
    return result.stdout.strip() if result.returncode==0 else ''


def github_name(url):
    patterns=(r'https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?',
              r'git@github\.com:([\w.-]+/[\w.-]+?)(?:\.git)?',
              r'ssh://git@github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?')
    for pattern in patterns:
        match=re.fullmatch(pattern,url)
        if match:
            name=match[1]
            if all(part not in ('.','..') for part in name.split('/')):
                return name
    return None


def discover(path):
    root=Path(git(Path(path).expanduser().resolve(),'rev-parse','--show-toplevel')).resolve()
    current=root;seen=set();chain=[]
    for _ in range(8):
        key=os.path.normcase(str(current))
        if key in seen:
            raise ValueError('Git 本地 remote 出现循环')
        seen.add(key)
        branch=git(current,'symbolic-ref','--quiet','--short','HEAD',allow_failure=True)
        remotes=git(current,'remote').splitlines()
        tracking=git(current,'config',f'branch.{branch}.remote',allow_failure=True) if branch else ''
        ordered=list(dict.fromkeys([r for r in (tracking,'origin',*remotes) if r and r in remotes]))
        found=[];local=[]
        for remote in ordered:
            url=git(current,'remote','get-url',remote)
            name=github_name(url)
            if name:
                found.append((remote,url,name))
            elif '://' not in url and not re.match(r'[^/\\]+@',url):
                candidate=Path(url)
                if not candidate.is_absolute():
                    candidate=current/candidate
                if candidate.exists():
                    local.append(candidate.resolve())
        if found:
            preferred=[f for f in found if f[0] in (tracking,'origin')]
            if not preferred and len({f[2].lower() for f in found})>1:
                raise ValueError('存在多个 GitHub remote，且没有 origin 或上游标记；请在仓库设置上游')
            remote,url,name=(preferred or found)[0]
            base=git(current,'config',f'branch.{branch}.gh-merge-base',allow_failure=True) if branch else ''
            if not base and branch in ('main','master','dev','develop','development'):
                merge=git(current,'config',f'branch.{branch}.merge',allow_failure=True)
                if tracking==remote and merge.startswith('refs/heads/'):
                    base=merge[len('refs/heads/'):]
            if not base:
                head=git(current,'symbolic-ref','--quiet','--short',f'refs/remotes/{remote}/HEAD',allow_failure=True)
                base=head[len(remote)+1:] if head.startswith(remote+'/') else ''
            if not base:
                # Read-only remote default-branch discovery; never infer a production base from an issue title.
                output=git(current,'ls-remote','--symref',url,'HEAD',allow_failure=True)
                match=re.search(r'^ref: refs/heads/(\S+)\s+HEAD$',output,re.M)
                base=match[1] if match else ''
            if base:
                git(current,'check-ref-format','refs/heads/'+base)
            return {'path':str(root),'name':root.name,'github_repository':name,'remote_url':url,
                    'base_branch':base or None,'branch':branch or None,'discovery':'git-remote',
                    'origin_chain':chain,'source_root':str(current)}
        if local:
            chain.append(str(current))
            current=Path(git(local[0],'rev-parse','--show-toplevel')).resolve()
            continue
        break
    else:
        raise ValueError('Git 本地 remote 链超过 8 层，请登记实际源仓库')
    return {'path':str(root),'name':root.name,'github_repository':None,'remote_url':None,
            'base_branch':branch or None,'branch':branch or None,'discovery':'local-only',
            'origin_chain':chain,'source_root':str(current)}


def adapter_for(path):
    root=Path(path)
    return 'kotlin-policy' if all((root/p).is_file() for p in
        ('src/BatteryPolicy.kt','acceptance/PolicyAcceptance.kt','app/PolicyProbe.kt')) else 'unconfigured'


class Registry:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS repositories (
                id TEXT PRIMARY KEY, path_key TEXT UNIQUE NOT NULL, metadata TEXT NOT NULL)''')

    def list(self):
        with self.store.connect() as db:
            items=[json.loads(r['metadata']) for r in db.execute('SELECT metadata FROM repositories ORDER BY id')]
        builtin={'id':'permission-policy-lab','name':'独立样例 · 权限策略','path':None,'github_repository':None,
            'base_branch':None,'adapter':'builtin','ready':True,'discovery':'isolated-fixture'}
        return [builtin,*items]

    def get(self,key):
        return next((r for r in self.list() if r['id']==key),None)

    def add(self,path,name=None):
        if not isinstance(path,str) or not path.strip():
            raise ValueError('请输入已有本地 Git 仓库的路径')
        info=discover(path)
        if name is not None and (not isinstance(name,str) or not 1<=len(name.strip())<=80):
            raise ValueError('仓库名称需要 1 至 80 个字符')
        key=os.path.normcase(str(Path(info['path'])))
        info.update(id='repo-'+hashlib.sha256(key.encode()).hexdigest()[:12],
                    adapter=adapter_for(info['path']))
        info['ready']=info['adapter']=='kotlin-policy'
        if name:
            info['name']=name.strip()
        with self.store.transaction() as db:
            previous=db.execute('SELECT metadata FROM repositories WHERE path_key=?',(key,)).fetchone()
            if previous:
                return json.loads(previous['metadata'])
            db.execute('INSERT INTO repositories(id,path_key,metadata) VALUES(?,?,?)',
                (info['id'],key,json.dumps(info,ensure_ascii=False)))
        return info

    def refresh(self,key):
        previous=self.get(key)
        if not previous or not previous.get('path'):
            raise ValueError('内置独立样例没有可刷新的远程仓库')
        info=discover(previous['path'])|{'id':key,'name':previous['name'],'adapter':adapter_for(previous['path'])}
        info['ready']=info['adapter']=='kotlin-policy'
        with self.store.transaction() as db:
            db.execute('UPDATE repositories SET metadata=? WHERE id=?',(json.dumps(info,ensure_ascii=False),key))
        return info

    def plan(self,project,include_pr=False):
        repo=self.get(project)
        if not repo:
            raise ValueError('工单关联仓库不存在')
        available=bool(repo.get('github_repository') and repo.get('base_branch'))
        if include_pr and not available:
            raise ValueError('当前仓库未发现 GitHub remote 和目标分支；不能把来源 issue 当成发布目标')
        target={k:repo[k] for k in ('github_repository','remote_url','base_branch')} if include_pr else None
        return {'repository_id':project,'local_test':True,'github_pr':target,'draft':True,
                'pr_available':available,'repository_name':repo['name']}
