"""Offline tests: real local Git transports, synthetic GitHub API and model receipts.

These tests never contact GitHub, create a real PR, or consume Codex usage.
"""
import copy
import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from codex_runner import TRACKED, CodexRunner, commit, git
from engine import BASE, CONFIG, Conflict, Store
from github_release import GitHubPublisher, PublicationError
from repositories import discover, github_name
from server import make_server

REMOTE='https://github.com/unit-tests/policy.git'
NAME='unit-tests/policy'
DIGEST='a'*64


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.repo=self.root/'source'
        shutil.copytree(BASE/'examples/permission-policy',self.repo)
        git(self.repo,'init','-b','main');git(self.repo,'config','core.autocrlf','false')
        self.base=commit(self.repo,'offline fixture baseline',TRACKED)

    def tearDown(self):
        self.temp.cleanup()

    def remote(self,repo=None,url=REMOTE):
        repo=repo or self.repo
        git(repo,'remote','add','origin',url)
        git(repo,'update-ref','refs/remotes/origin/main',self.base)
        git(repo,'symbolic-ref','refs/remotes/origin/HEAD','refs/remotes/origin/main')


class RepositoryTests(Fixture):
    def setUp(self):
        super().setUp()
        self.store=Store(self.root/'state.db',CONFIG|{'mode':'codex'})

    def gate(self,project='permission-policy-lab'):
        task=self.store.create({'title':'offline registry test','project':project},'registry-gate')
        self.store.action(task,'start')
        evidence={'simulated':False,'evidence':[{'thread_id':'test-only','exit_code':0}]}
        self.store.finish(self.store.claim(),evidence)
        self.store.finish(self.store.claim(),evidence|{'artifact_digest':DIGEST})
        self.store.finish(self.store.claim(),evidence|{'passed':True,'artifact_digest':DIGEST,
            'commit':self.base,'checks':{'passed':True,'compile':{'exit_code':0},'tests':{'exit_code':0},
            'total':20,'passed_count':20,'failed_count':0,'jar_sha256':'b'*64,'input_attestation':{
                'candidate_digest':DIGEST,'candidate_commit':self.base,'jar_sha256':'b'*64}}})
        return task

    def test_github_url_forms_and_unrelated_hosts(self):
        for url in (REMOTE,'git@github.com:unit-tests/policy.git','ssh://git@github.com/unit-tests/policy.git'):
            self.assertEqual(github_name(url),NAME)
        for url in ('https://github.com.attacker.test/a/b','https://token@github.com/a/b',
                    'https://github.com/a/b/issues/1','https://github.com/a/..','/local/path'):
            self.assertIsNone(github_name(url))

    def test_multiple_repositories_deduplicate_and_persist(self):
        first=self.store.repositories.add(str(self.repo),'策略一')
        other=self.root/'other';git(self.root,'clone','--',str(self.repo),str(other))
        second=self.store.repositories.add(str(other),'策略二')
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(self.store.repositories.add(str(self.repo/'src'))['id'],first['id'])
        reopened=Store(self.store.path,CONFIG|{'mode':'codex'})
        self.assertEqual(len(reopened.repositories.list()),3)
        task=reopened.create({'title':'bound repository','project':second['id']},'bound-task')
        self.assertEqual(reopened.detail(task)['task']['project'],second['id'])
        self.assertEqual(git(self.repo,'status','--porcelain'),'')

    def test_local_origin_chain_discovers_actual_github_repo(self):
        self.remote()
        clone=self.root/'clone';git(self.root,'clone','--',str(self.repo),str(clone))
        info=discover(clone)
        self.assertEqual(info['github_repository'],NAME)
        self.assertEqual(info['base_branch'],'main')
        self.assertEqual(Path(info['source_root']),self.repo)
        self.assertEqual(len(info['origin_chain']),1)

    def test_local_origin_cycle_is_rejected(self):
        clone=self.root/'clone';git(self.root,'clone','--',str(self.repo),str(clone))
        git(self.repo,'remote','add','origin',str(clone))
        with self.assertRaisesRegex(ValueError,'循环'):
            discover(clone)

    def test_ambiguous_github_remotes_are_not_guessed(self):
        git(self.repo,'remote','add','first',REMOTE)
        git(self.repo,'remote','add','second','https://github.com/another/project.git')
        with self.assertRaisesRegex(ValueError,'多个 GitHub'):
            discover(self.repo)

    def test_tracked_development_base_is_preferred(self):
        self.remote();git(self.repo,'checkout','-b','dev')
        git(self.repo,'config','branch.dev.remote','origin')
        git(self.repo,'config','branch.dev.merge','refs/heads/dev')
        self.assertEqual(discover(self.repo)['base_branch'],'dev')
        git(self.repo,'checkout','-b','feature/one')
        self.assertEqual(discover(self.repo)['base_branch'],'main')

    def test_unconfigured_repo_can_register_but_cannot_execute(self):
        other=self.root/'unconfigured';other.mkdir();git(other,'init','-b','main')
        info=self.store.repositories.add(str(other))
        self.assertFalse(info['ready'])
        with self.assertRaisesRegex(ValueError,'验收'):
            self.store.create({'title':'do not run unknown commands','project':info['id']},'unconfigured')
        with self.assertRaises(ValueError):
            self.store.repositories.add(str(self.root/'missing'))

    def test_builtin_release_never_infers_remote_from_issue(self):
        task=self.gate();preview=self.store.release_preview(task)
        self.assertFalse(preview['plan']['pr_available'])
        with self.assertRaisesRegex(ValueError,'来源 issue'):
            self.store.action(task,'approve',include_pr=True,plan_digest=preview['plan_digest'])
        self.store.action(task,'approve',actor='explicit-local-test-policy')
        self.assertIsNone(self.store.detail(task)['task']['approved_release_plan']['github_pr'])

    def test_pr_approval_binds_refreshed_repository_and_candidate(self):
        self.remote();info=self.store.repositories.add(str(self.repo))
        task=self.gate(info['id']);preview=self.store.release_preview(task)
        with self.assertRaises(Conflict):
            self.store.action(task,'approve',include_pr=True)
        git(self.repo,'config','branch.main.gh-merge-base','dev')
        refreshed=self.store.release_preview(task)
        self.assertEqual(refreshed['plan']['github_pr']['base_branch'],'dev')
        with self.assertRaises(Conflict):
            self.store.action(task,'approve',include_pr=True,plan_digest=preview['plan_digest'])
        self.store.action(task,'approve',include_pr=True,plan_digest=refreshed['plan_digest'])
        plan=self.store.detail(task)['task']['approved_release_plan']
        self.assertEqual(plan['artifact_digest'],DIGEST)
        self.assertEqual(plan['github_pr']['github_repository'],NAME)

    def test_http_registration_changes_etag_and_validates_input(self):
        server=make_server(self.store,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,body=None,headers=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=10)
            connection.request(method,path,json.dumps(body) if body is not None else None,headers or {})
            response=connection.getresponse();data=response.read()
            result=response.status,dict(response.getheaders()),json.loads(data) if data else None
            connection.close();return result
        headers={'Content-Type':'application/json','X-RSI-Client':'local-dashboard'}
        try:
            initial=request('GET','/api/snapshot')[1]['ETag']
            self.assertEqual(request('POST','/api/repositories',{'path':str(self.repo)})[0],403)
            status,_,repo=request('POST','/api/repositories',{'path':str(self.repo)},headers)
            self.assertEqual(status,200)
            self.assertEqual(request('GET','/api/snapshot',headers={'If-None-Match':initial})[0],200)
            self.assertEqual(len(request('GET','/api/repositories')[2]['items']),2)
            self.assertEqual(request('POST',f'/api/repositories/{repo["id"]}/refresh',{},headers)[0],200)
            self.assertEqual(request('POST','/api/repositories',{'path':str(self.repo),'command':'bad'},headers)[0],400)
            task=self.gate(repo['id'])
            self.assertEqual(request('GET',f'/api/tasks/{task}/release')[0],200)
            self.assertEqual(request('POST',f'/api/tasks/{task}/approve',{'include_pr':'yes'},headers)[0],400)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_registered_execution_clones_and_preserves_source(self):
        info=self.store.repositories.add(str(self.repo))
        runner=CodexRunner.__new__(CodexRunner);runner.store=self.store
        runner.kotlin=SimpleNamespace(build_test=lambda *args:{'compile':{'exit_code':0},
            'passed':False,'test_output':'synthetic failing baseline'})
        runner.ask=lambda *args:{'response':{'decision':'ready'},'thread_id':'offline-only'}
        folder=self.root/'execution';folder.mkdir()
        result=runner.analyzing({'project':info['id']},folder)
        self.assertEqual(Path(git(Path(result['repo']),'remote','get-url','origin')).resolve(),self.repo.resolve())
        self.assertEqual(result['commit'],self.base)
        self.assertEqual(git(self.repo,'status','--porcelain'),'')
        self.assertEqual(len(result['evidence']),3)

    def test_registered_execution_rejects_dirty_source(self):
        info=self.store.repositories.add(str(self.repo))
        (self.repo/'user-work.txt').write_text('preserve this',encoding='utf-8')
        runner=CodexRunner.__new__(CodexRunner);runner.store=self.store
        folder=self.root/'execution';folder.mkdir()
        with self.assertRaisesRegex(RuntimeError,'uncommitted'):
            runner.analyzing({'project':info['id']},folder)
        self.assertFalse((folder/'repo').exists())
        self.assertEqual((self.repo/'user-work.txt').read_text(encoding='utf-8'),'preserve this')

    def test_authorized_pr_requires_matching_release_receipt(self):
        self.remote();info=self.store.repositories.add(str(self.repo));task=self.gate(info['id'])
        preview=self.store.release_preview(task)
        self.store.action(task,'approve',include_pr=True,plan_digest=preview['plan_digest'])
        job=self.store.claim()
        result={'simulated':False,'evidence':[{'thread_id':'test-only','exit_code':0}],
            'artifact_digest':DIGEST,'release':{'manifest':{'commit':self.base,'files':{'candidate.jar':'b'*64}}}}
        with self.assertRaisesRegex(Conflict,'GitHub PR'):
            self.store.finish(job,result)
        result['release']['pull_request']={'status':'published','repository':NAME,'base':'main','head_sha':'0'*40}
        with self.assertRaisesRegex(Conflict,'GitHub PR'):
            self.store.finish(job,result)
        self.assertEqual(self.store.detail(task)['task']['state'],'releasing')

    def test_external_receipt_survives_stop_without_completing_task(self):
        task=self.gate();self.store.action(task,'approve');job=self.store.claim()
        self.store.action(task,'stop')
        receipt={'identity':'test-only','url':'https://github.com/unit-tests/policy/pull/23'}
        self.store.record_publication(job,receipt);self.store.record_publication(job,receipt)
        detail=self.store.detail(task)
        self.assertEqual(detail['task']['state'],'stopped')
        self.assertEqual(detail['publications'],[receipt])


class LocalGitAndFakeGitHub:
    """Route Git transport to a local bare repo; replace ONLY the GitHub service."""
    def __init__(self,bare):
        self.bare=bare;self.pr=None;self.posts=0;self.calls=[];self.ambiguous_create=False
        self.race_branch=None

    def __call__(self,argv,repo):
        self.calls.append(argv)
        if argv[0]=='git':
            args=[str(self.bare) if arg==REMOTE else arg for arg in argv]
            result=subprocess.run(args,cwd=repo,capture_output=True,text=True,encoding='utf-8',timeout=15)
            if result.returncode:
                raise PublicationError('Local Git test transport rejected: '+result.stderr)
            if self.race_branch and argv[1]=='ls-remote' and not result.stdout:
                git(self.bare,'update-ref',self.race_branch,git(self.bare,'rev-parse','main'))
                self.race_branch=None
            return result.stdout.strip()
        if argv[:2]==['gh','api']:
            if '?' in argv[-1]:
                return json.dumps([self.pr] if self.pr else [])
            return json.dumps(self.pr)
        if argv[:3]==['gh','pr','create']:
            self.posts+=1
            branch=argv[argv.index('--head')+1];base=argv[argv.index('--base')+1]
            body=Path(argv[argv.index('--body-file')+1]).read_text(encoding='utf-8')
            self.pr={'number':23,'html_url':f'https://github.com/{NAME}/pull/23','state':'open',
                'draft':'--draft' in argv,'body':body,'head':{'sha':git(self.bare,'rev-parse',branch),
                'ref':branch,'repo':{'full_name':NAME}},'base':{'ref':base,'sha':git(self.bare,'rev-parse',base),
                'repo':{'full_name':NAME}}}
            if self.ambiguous_create:
                raise TimeoutError('Synthetic lost response after server accepted PR')
            return self.pr['html_url']
        raise AssertionError('Unexpected test command: '+str(argv))


class GitHubPublicationTests(Fixture):
    def setUp(self):
        super().setUp()
        self.bare=self.root/'remote.git';git(self.root,'clone','--bare','--',str(self.repo),str(self.bare))
        self.remote()
        self.candidate_repo=self.root/'candidate';git(self.root,'clone','--',str(self.repo),str(self.candidate_repo))
        source=self.candidate_repo/'src/BatteryPolicy.kt'
        source.write_text(source.read_text(encoding='utf-8')+'\n// Offline transport test change.\n',encoding='utf-8')
        revision=commit(self.candidate_repo,'offline transport candidate',['src/BatteryPolicy.kt'])
        self.candidate={'repo':str(self.candidate_repo),'commit':revision,'artifact_digest':DIGEST}
        self.verified={'passed':True,'artifact_digest':DIGEST,'commit':revision,
            'checks':{'compile':{'exit_code':0},'passed_count':20,'total':20,'jar_sha256':'b'*64}}
        self.plan={'artifact_digest':DIGEST,'github_pr':{'github_repository':NAME,'base_branch':'main','remote_url':REMOTE}}
        self.job={'id':7,'title':'offline publication test','repair_count':1,'approved_digest':DIGEST}
        self.transport=LocalGitAndFakeGitHub(self.bare)
        self.publisher=GitHubPublisher(lambda:True,self.transport)
        self.folder=self.root/'publication'
        self.branch=f'rsi/task-7-{revision[:12]}'

    def publish(self):
        return self.publisher.publish(self.job,self.candidate,self.verified,self.plan,self.folder)

    def test_publish_exact_commit_and_retry_reuses_draft_pr(self):
        result=self.publish();again=self.publish()
        self.assertEqual(result,again);self.assertEqual(self.transport.posts,1)
        self.assertEqual(git(self.bare,'rev-parse',self.branch),self.candidate['commit'])
        self.assertEqual(git(self.bare,'rev-parse','main'),self.base)
        self.assertTrue(result['draft']);self.assertEqual(result['ci_status'],'not-checked')
        self.assertIn('rsi-publication:',self.transport.pr['body'])
        self.assertEqual(self.publisher.observe(result,self.candidate_repo,self.root/'observation')['head_sha'],self.candidate['commit'])

    def test_ambiguous_create_recovers_without_duplicate_post(self):
        self.transport.ambiguous_create=True
        self.assertEqual(self.publish()['status'],'published')
        self.assertEqual(self.publish()['status'],'published')
        self.assertEqual(self.transport.posts,1)

    def test_existing_different_branch_is_never_overwritten(self):
        git(self.bare,'update-ref','refs/heads/'+self.branch,self.base)
        with self.assertRaisesRegex(PublicationError,'不会覆盖'):
            self.publish()
        self.assertEqual(git(self.bare,'rev-parse',self.branch),self.base)
        self.assertEqual(self.transport.posts,0)

    def test_concurrent_branch_creation_is_not_overwritten(self):
        self.transport.race_branch='refs/heads/'+self.branch
        with self.assertRaises(PublicationError):
            self.publish()
        self.assertEqual(git(self.bare,'rev-parse',self.branch),self.base)
        self.assertEqual(self.transport.posts,0)

    def test_remote_metadata_change_requires_new_plan(self):
        git(self.repo,'remote','set-url','origin','https://github.com/wrong/target.git')
        with self.assertRaisesRegex(PublicationError,'发布授权不同'):
            self.publish()
        self.assertFalse(self.transport.calls)

    def test_stale_base_or_changes_outside_scope_block_publication(self):
        source=self.repo/'src/BatteryPolicy.kt';source.write_text('// moved base\n',encoding='utf-8')
        commit(self.repo,'advance remote base',['src/BatteryPolicy.kt'])
        git(self.repo,'push',str(self.bare),'main')
        with self.assertRaises(PublicationError):
            self.publish()
        self.assertEqual(self.transport.posts,0)
        self.assertFalse(any(c[0]=='git' and c[1]=='push' for c in self.transport.calls))

    def test_out_of_scope_commit_is_rejected(self):
        (self.candidate_repo/'extra.txt').write_text('out of scope',encoding='utf-8')
        revision=commit(self.candidate_repo,'out of scope',['extra.txt'])
        self.candidate['commit']=self.verified['commit']=revision
        with self.assertRaisesRegex(PublicationError,'文件范围'):
            self.publish()
        self.assertEqual(self.transport.posts,0)

    def test_wrong_pr_identity_or_sha_is_not_reported_as_success(self):
        self.publish()
        original=copy.deepcopy(self.transport.pr)
        for key,value in (('sha',self.base),('ref','wrong-branch')):
            self.transport.pr=copy.deepcopy(original);self.transport.pr['head'][key]=value
            with self.assertRaises(PublicationError):
                self.publish()
        self.transport.pr=copy.deepcopy(original);self.transport.pr['draft']=False
        with self.assertRaises(PublicationError):
            self.publish()
        self.assertEqual(self.transport.posts,1)

    def test_unverified_or_cancelled_work_has_no_side_effect(self):
        self.verified['commit']=self.base
        with self.assertRaises(PublicationError):
            self.publish()
        self.assertFalse(self.transport.calls)
        self.verified['commit']=self.candidate['commit']
        self.publisher.heartbeat=lambda:False
        with self.assertRaises(PublicationError):
            self.publish()
        self.assertFalse(self.transport.calls)

    def test_local_only_plan_does_not_call_git_or_github(self):
        self.plan['github_pr']=None
        self.assertEqual(self.publish(),{'status':'not-requested'})
        self.assertFalse(self.transport.calls)


if __name__=='__main__':
    unittest.main()
