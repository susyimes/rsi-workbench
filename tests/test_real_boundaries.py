"""Offline boundary tests. Synthetic receipts here are NOT real Codex evidence."""
import http.client
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from codex_client import Cancelled
from codex_runner import ALLOWED, TRACKED, CodexRunner, apply_replacement, commit, enforce_candidate, git, protected_manifest
from engine import BASE, CONFIG, Conflict, DemoRunner, Store
from release_server import make_release, verify_release
from toolchain import command, sha, write_json

FAKE_RECEIPT={'thread_id':'unit-test-only-not-a-real-session','exit_code':0}


class RealBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'state.db',CONFIG|{'mode':'codex'})

    def tearDown(self):
        self.temp.cleanup()

    def task(self):
        task=self.store.create({'title':'unit boundary'},'one')
        self.store.action(task,'start')
        return task

    def submit(self,**result):
        job=self.store.claim()
        self.store.finish(job,{'simulated':False,'evidence':[FAKE_RECEIPT]}|result)
        return job

    def test_demo_history_cannot_be_relabelled(self):
        with self.assertRaises(ValueError):
            Store(self.store.path,CONFIG)

    def test_arbitrary_project_or_scenario_is_rejected(self):
        for body in ({'title':'x','project':'production'},{'title':'x','scenario':'pass'},
                     {'title':'x','command':'echo unsafe'}):
            with self.assertRaises(ValueError):
                self.store.create(body,'bad')

    def test_demo_result_and_missing_session_cannot_advance_real_task(self):
        task=self.task();job=self.store.claim()
        with self.assertRaises(ValueError):
            self.store.finish(job,DemoRunner().run(job))
        with self.assertRaises(ValueError):
            self.store.finish(job,{'simulated':False})
        self.assertEqual(self.store.detail(task)['task']['state'],'analyzing')

    def test_model_success_without_actual_checks_is_rejected(self):
        task=self.task();self.submit();self.submit(artifact_digest='a'*64)
        job=self.store.claim()
        with self.assertRaises(Conflict):
            self.store.finish(job,{'simulated':False,'evidence':[FAKE_RECEIPT],'passed':True,'artifact_digest':'a'*64})
        self.assertEqual(self.store.detail(task)['task']['state'],'verifying')

    def test_publication_effect_is_fenced_after_stop(self):
        task=self.task();job=self.store.claim();self.store.action(task,'stop')
        called=[]
        with self.assertRaises(Conflict):
            self.store.guarded(job,lambda:called.append(True))
        self.assertFalse(called)

    def test_resume_preserves_analysis_and_repair_count(self):
        task=self.task();self.submit();job=self.store.claim()
        self.store.finish(job,{'simulated':False,'error':'InfrastructureError'})
        self.store.action(task,'resume')
        self.assertEqual(self.store.detail(task)['task']['state'],'developing')
        self.assertEqual(len(self.store.detail(task)['runs']),2)

    def test_validation_failures_cannot_bypass_repair_limit_with_resume(self):
        task=self.task();self.submit()
        for _ in range(CONFIG['max_repairs']+1):
            self.submit(artifact_digest='a'*64)
            self.submit(passed=False,artifact_digest='a'*64)
        with self.assertRaises(Conflict):
            self.store.action(task,'resume')

    def gate(self):
        task=self.task();self.submit();self.submit(artifact_digest='a'*64)
        self.submit(passed=True,artifact_digest='a'*64,commit='c'*40,checks={
            'passed':True,'compile':{'exit_code':0},'tests':{'exit_code':0},'total':20,'passed_count':20,
            'failed_count':0,'jar_sha256':'b'*64,'input_attestation':{
                'candidate_digest':'a'*64,'candidate_commit':'c'*40,'jar_sha256':'b'*64}})
        self.store.action(task,'approve')
        return task

    def test_changed_published_binary_is_rejected(self):
        task=self.gate();job=self.store.claim()
        with self.assertRaises(Conflict):
            self.store.finish(job,{'simulated':False,'evidence':[FAKE_RECEIPT],'artifact_digest':'a'*64,
                'release':{'manifest':{'commit':'c'*40,'files':{'candidate.jar':'d'*64}}}})
        self.assertEqual(self.store.detail(task)['task']['state'],'releasing')

    def test_reverification_clears_previous_approval(self):
        task=self.gate();self.submit(error='PublisherBlocked')
        self.store.action(task,'reverify')
        value=self.store.detail(task)['task']
        self.assertEqual(value['candidate_digest'],'a'*64)
        self.assertIsNone(value['verified_digest']);self.assertIsNone(value['approved_digest'])
        with self.assertRaises(Conflict):
            self.store.action(task,'approve')

    def test_failed_observation_cannot_be_done(self):
        task=self.gate()
        self.submit(artifact_digest='a'*64,release={'manifest':{'commit':'c'*40,'files':{'candidate.jar':'b'*64}}})
        self.submit(passed=False,artifact_digest='a'*64)
        detail=self.store.detail(task)
        self.assertEqual(detail['task']['state'],'blocked');self.assertFalse(detail['lessons'])

    def test_new_analysis_does_not_reuse_an_old_candidate(self):
        task=self.task();self.submit();self.submit(artifact_digest='a'*64)
        self.submit(error='InfrastructureError')
        self.store.action(task,'retry');self.submit()
        runner=CodexRunner.__new__(CodexRunner);runner.store=self.store
        self.assertIsNone(runner.prior({'id':task},'developing'))

    def fixture(self):
        repo=self.root/'repo';shutil.copytree(BASE/'examples/permission-policy',repo)
        git(repo,'init','-b','main');git(repo,'config','core.autocrlf','false')
        head=commit(repo,'unit baseline',TRACKED)
        return repo,head

    def test_clone_preserves_protected_bytes_and_edit_allowlist(self):
        repo,head=self.fixture();manifest=protected_manifest(repo)
        copy=self.root/'clone'
        git(self.root,'-c','core.autocrlf=false','clone','--no-hardlinks','--',str(repo),str(copy))
        enforce_candidate(copy,head,manifest)
        (copy/'acceptance/PolicyAcceptance.kt').write_text('weakened',encoding='utf-8')
        with self.assertRaises(RuntimeError):
            enforce_candidate(copy,head,manifest)

    def test_replacement_is_bound_to_path_and_base_hash(self):
        repo,_=self.fixture();base=sha(repo/ALLOWED)
        for patch in ({'path':'../outside','base_sha256':base,'content':'bad'},
                      {'path':ALLOWED,'base_sha256':'wrong','content':'bad'},
                      {'path':ALLOWED,'base_sha256':base,'content':''}):
            with self.assertRaises(RuntimeError):
                apply_replacement(repo,base,patch)
            self.assertEqual(sha(repo/ALLOWED),base)
        apply_replacement(repo,base,{'path':ALLOWED,'base_sha256':base,'content':'// permitted replacement\n'})
        with self.assertRaises(RuntimeError):
            apply_replacement(repo,base,{'path':ALLOWED,'base_sha256':base,'content':'// stale\n'})

    def test_trusted_command_timeout_terminates_owned_process(self):
        started=time.monotonic()
        with self.assertRaises(TimeoutError):
            command([sys.executable,'-c','import time; time.sleep(20)'],self.root,self.root/'timeout.log',timeout=.3)
        self.assertLess(time.monotonic()-started,5)

    def test_trusted_command_cancellation_terminates_owned_process(self):
        beats=iter([True,False])
        with self.assertRaises(Cancelled):
            command([sys.executable,'-c','import time; time.sleep(20)'],self.root,self.root/'cancel.log',heartbeat=lambda:next(beats,False))

    def test_release_health_rejects_changed_bytes(self):
        release=self.root/'release';release.mkdir()
        for name in ('candidate.jar','kotlin-stdlib.jar'):
            (release/name).write_bytes(b'unit-test-not-real-jar')
        write_json(release/'manifest.json',{'commit':'unit','artifact_digest':'a'*64,
            'files':{name:sha(release/name) for name in ('candidate.jar','kotlin-stdlib.jar')}})
        server=make_release(release,'unused-java')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request():
            conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=3)
            conn.request('GET','/health');response=conn.getresponse();response.read();status=response.status;conn.close();return status
        try:
            self.assertEqual(request(),200)
            (release/'candidate.jar').write_bytes(b'tampered')
            self.assertEqual(request(),503)
            with self.assertRaises(ValueError):
                verify_release(release)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    unittest.main()
