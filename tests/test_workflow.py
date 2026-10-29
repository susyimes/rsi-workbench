import concurrent.futures
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from engine import CONFIG, Conflict, DemoRunner, Store
from server import make_server


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'state.db')
        self.runner = DemoRunner()

    def tearDown(self):
        self.temp.cleanup()

    def create(self, scenario='pass', project='demo', key='test'):
        return self.store.create({'title':'验证工作流','scenario':scenario,'project':project},key)

    def step(self):
        job = self.store.claim()
        self.assertIsNotNone(job)
        self.assertTrue(self.store.finish(job,self.runner.run(job)))
        return job

    def to_gate(self, task_id):
        self.store.action(task_id,'start')
        for _ in range(3):
            self.step()
        self.assertEqual(self.store.detail(task_id)['task']['state'],'awaiting_release')

    def test_persistent_full_loop_and_version_bound_approval(self):
        task_id = self.create()
        self.to_gate(task_id)
        self.assertIsNone(self.store.claim())
        task = self.store.detail(task_id)['task']
        self.assertEqual(task['candidate_digest'],task['verified_digest'])
        self.store.action(task_id,'approve')
        self.step()
        self.step()
        reopened = Store(self.store.path)
        detail = reopened.detail(task_id)
        self.assertEqual(detail['task']['state'],'done')
        self.assertEqual(detail['approvals'][0]['artifact_digest'],detail['task']['candidate_digest'])
        self.assertEqual(len(detail['lessons']),1)
        self.assertEqual(reopened.snapshot()['summary']['done'],1)

    def test_repair_loop_invalidates_old_candidate(self):
        task_id = self.create('repair_once')
        self.store.action(task_id,'start')
        self.step(); self.step()
        before = self.store.detail(task_id)['task']['candidate_digest']
        self.step()
        failed = self.store.detail(task_id)['task']
        self.assertEqual(failed['state'],'developing')
        self.assertIsNone(failed['candidate_digest'])
        self.step(); self.step()
        repaired = self.store.detail(task_id)['task']
        self.assertEqual(repaired['state'],'awaiting_release')
        self.assertNotEqual(before,repaired['candidate_digest'])
        self.assertEqual(repaired['repair_count'],1)

    def test_persistent_failure_is_bounded(self):
        task_id = self.create('always_fail')
        self.store.action(task_id,'start')
        for _ in range(7):
            self.step()
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.detail(task_id)['task']['state'],'blocked')
        self.assertEqual(self.store.snapshot()['summary']['done'],0)
        with self.assertRaises(Conflict):
            self.store.action(task_id,'approve')

    def test_stop_fences_inflight_completion(self):
        task_id = self.create()
        self.store.action(task_id,'start')
        job = self.store.claim()
        self.store.action(task_id,'stop')
        self.assertFalse(self.store.finish(job,self.runner.run(job)))
        self.assertEqual(self.store.detail(task_id)['task']['state'],'stopped')
        self.assertIsNone(self.store.claim())

    def test_duplicate_completion_does_not_append_events(self):
        task_id = self.create()
        self.store.action(task_id,'start')
        job = self.step()
        before = self.store.snapshot()['revision']
        self.assertFalse(self.store.finish(job,self.runner.run(job)))
        self.assertEqual(self.store.snapshot()['revision'],before)

    def test_atomic_idempotency_under_concurrency(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _:self.create(),range(16)))
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(self.store.snapshot()['summary']['total'],1)
        with self.assertRaises(Conflict):
            self.store.create({'title':'另一张工单'},'test')

    def test_one_project_cannot_be_claimed_twice(self):
        first = self.create(key='one')
        second = self.create(key='two')
        self.store.action(first,'start'); self.store.action(second,'start')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(lambda _:self.store.claim(),range(4)))
        self.assertEqual(sum(job is not None for job in jobs),1)

    def test_different_projects_can_run_concurrently(self):
        first = self.create(key='one',project='first')
        second = self.create(key='two',project='second')
        self.store.action(first,'start'); self.store.action(second,'start')
        self.assertIsNotNone(self.store.claim())
        self.assertIsNotNone(self.store.claim())

    def expire(self, job):
        with self.store.transaction() as db:
            db.execute('UPDATE tasks SET lease_until=? WHERE id=?',(time.time()-1,job['id']))
            db.execute('UPDATE project_locks SET lease_until=? WHERE token=?',(time.time()-1,job['lease_token']))

    def test_expired_lease_recovery_rejects_old_worker(self):
        task_id = self.create(); self.store.action(task_id,'start')
        old = self.store.claim(); self.expire(old)
        self.assertFalse(self.store.heartbeat(old['lease_token']))
        new = self.store.claim()
        self.assertNotEqual(old['lease_token'],new['lease_token'])
        self.assertFalse(self.store.finish(old,self.runner.run(old)))
        self.assertTrue(self.store.finish(new,self.runner.run(new)))

    def test_crash_recovery_has_a_limit(self):
        task_id = self.create(); self.store.action(task_id,'start')
        for _ in range(CONFIG['max_recoveries']+1):
            job=self.store.claim(); self.expire(job)
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.detail(task_id)['task']['state'],'blocked')

    def test_cannot_release_unverified_or_modified_candidate(self):
        task_id=self.create()
        with self.assertRaises(Conflict):
            self.store.action(task_id,'approve')
        self.to_gate(task_id)
        with self.store.transaction() as db:
            db.execute("UPDATE tasks SET candidate_digest='changed' WHERE id=?",(task_id,))
        with self.assertRaises(Conflict):
            self.store.action(task_id,'approve')
        self.assertIsNone(self.store.claim())

    def test_rejected_verification_does_not_mutate_state(self):
        task_id=self.create();self.store.action(task_id,'start');self.step();self.step()
        job=self.store.claim()
        with self.assertRaises(Conflict):
            self.store.finish(job,{'simulated':True,'passed':True,'artifact_digest':'wrong'})
        self.assertEqual(self.store.detail(task_id)['task']['state'],'verifying')

    def test_resident_roles_are_not_counted_as_active(self):
        value=self.store.snapshot()
        self.assertEqual(value['summary']['resident'],4)
        self.assertEqual(value['summary']['active'],0)
        self.assertEqual(value['summary']['total'],0)
        task_id=self.create();self.store.action(task_id,'stop')
        value=self.store.snapshot()
        self.assertEqual(value['summary']['done'],0)
        self.assertEqual(value['summary']['stopped'],1)
        self.assertNotIn('lease_token',value['items'][0])
        self.assertNotIn('idempotency_key',value['items'][0])

    def test_user_title_is_data(self):
        title='<script>fetch("/api/tasks")</script>'
        task_id=self.store.create({'title':title},'title-as-data')
        self.assertEqual(self.store.detail(task_id)['task']['title'],title)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.server=make_server(Store(Path(self.temp.name)/'db'),0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.port=self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.temp.cleanup()

    def request(self,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse()
        result=(response.status,dict(response.getheaders()),response.read())
        connection.close()
        return result

    def test_snapshot_etag_and_no_path_escape(self):
        status,headers,body=self.request('GET','/api/snapshot')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['mode'],'demo')
        self.assertEqual(self.request('GET','/api/snapshot',headers={'If-None-Match':headers['ETag']})[0],304)
        self.assertEqual(self.request('GET','/../engine.py')[0],404)

    def test_mutation_requires_local_origin_and_custom_header(self):
        body=json.dumps({'title':'HTTP test'})
        headers={'Content-Type':'application/json','X-RSI-Client':'local-dashboard','Idempotency-Key':'http-test'}
        self.assertEqual(self.request('POST','/api/tasks',body,{'Content-Type':'application/json'})[0],403)
        self.assertEqual(self.request('POST','/api/tasks',body,headers|{'Origin':'https://example.com'})[0],403)
        status,_,data=self.request('POST','/api/tasks',body,headers)
        self.assertEqual(status,200)
        task_id=json.loads(data)['id']
        self.assertEqual(self.request('POST',f'/api/tasks/{task_id}/approve','{}',headers)[0],409)
        self.assertEqual(self.request('POST',f'/api/tasks/{task_id}/start','{}',headers)[0],200)
        self.assertEqual(self.request('GET','/api/snapshot',headers={'Host':'evil.example'})[0],403)


if __name__ == '__main__':
    unittest.main()
