"""Persistent bounded workflow with separate real and demonstration databases."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / 'config.json').read_text(encoding='utf-8'))
RUNNABLE = ('analyzing', 'developing', 'verifying', 'releasing', 'observing')
LABELS = {'pending':'待处理','analyzing':'分析中','developing':'开发中',
          'verifying':'验证中','awaiting_release':'待发布确认','releasing':'发布中',
          'observing':'反馈观察','done':'已完成','blocked':'已阻塞','stopped':'已停止'}
ROLE = {'analyzing':'分析员','developing':'开发员','verifying':'验证员',
        'releasing':'发布员','observing':'维护专员'}


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path, config=None):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.config = dict(CONFIG if config is None else config)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
              description TEXT NOT NULL, project TEXT NOT NULL, scenario TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 2,
              repair_count INTEGER NOT NULL DEFAULT 0, recoveries INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              lease_token TEXT, lease_until REAL, candidate_digest TEXT,
              verified_digest TEXT, approved_digest TEXT,
              idempotency_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id),
              stage TEXT NOT NULL, role TEXT NOT NULL, token TEXT UNIQUE NOT NULL,
              started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
              output TEXT, simulated INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id),
              at TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_locks (
              project TEXT PRIMARY KEY, token TEXT NOT NULL, lease_until REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
              id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id),
              artifact_digest TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lessons (
              id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id),
              at TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed'
            );
            CREATE INDEX IF NOT EXISTS task_queue ON tasks(state, priority, id);
            CREATE INDEX IF NOT EXISTS event_task ON events(task_id, id);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS publications (
                identity TEXT PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id),
                artifact_digest TEXT NOT NULL, receipt TEXT NOT NULL, at TEXT NOT NULL);
            ''')
            mode=self.config.get('mode','demo')
            stored=db.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
            if stored and stored['value']!=mode:
                raise ValueError('Database mode differs; use a separate data directory')
            if not stored:
                if mode!='demo' and db.execute('SELECT count(*) FROM tasks').fetchone()[0]:
                    raise ValueError('Existing demonstration history cannot become real history')
                db.execute("INSERT INTO metadata(key,value) VALUES('mode',?)",(mode,))
            columns={r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
            if 'approved_release_plan' not in columns:
                db.execute('ALTER TABLE tasks ADD COLUMN approved_release_plan TEXT')
        from repositories import Registry
        self.repositories=Registry(self)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def event(db, task_id, kind, message):
        db.execute('INSERT INTO events(task_id,at,kind,message) VALUES(?,?,?,?)',
                   (task_id, utc(), kind, message))

    def create(self, payload, key):
        if not isinstance(payload, dict):
            raise ValueError('请求必须是 JSON 对象')
        if set(payload) - {'title','description','project','scenario','priority'}:
            raise ValueError('请求含未支持的字段')
        title = payload.get('title', '')
        description = payload.get('description', '')
        real=self.config.get('mode')=='codex'
        project = payload.get('project', 'permission-policy-lab' if real else 'demo-site')
        scenario = payload.get('scenario', 'permission_policy' if real else 'pass')
        priority = payload.get('priority', 2)
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise ValueError('标题需要 1 至 120 个字符')
        if not isinstance(description, str) or len(description) > 1500:
            raise ValueError('描述最多 1500 个字符')
        if not isinstance(project, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,60}', project):
            raise ValueError('项目标识使用英文、数字、点、横线或下划线')
        if real:
            repository=self.repositories.get(project)
            if not repository or not repository['ready'] or scenario!='permission_policy':
                raise ValueError('请选择已接入 Kotlin 策略验收的仓库；其他仓库可登记，但需要项目执行适配器')
        if not real and scenario not in ('pass', 'repair_once', 'always_fail'):
            raise ValueError('未知演示场景')
        if type(priority) is not int or priority not in (0, 1, 2, 3):
            raise ValueError('优先级必须为 0 至 3')
        if not isinstance(key, str) or not 1 <= len(key) <= 160:
            raise ValueError('缺少有效的幂等键')
        request_hash = digest(payload)
        with self.transaction() as db:
            prior = db.execute('SELECT id,request_hash FROM tasks WHERE idempotency_key=?',(key,)).fetchone()
            if prior:
                if prior['request_hash'] != request_hash:
                    raise Conflict('同一幂等键不能用于不同工单')
                return prior['id']
            now = utc()
            task_id = db.execute('''INSERT INTO tasks
                (title,description,project,scenario,priority,created_at,updated_at,idempotency_key,request_hash)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (title.strip(),description,project,scenario,priority,now,now,key,request_hash)).lastrowid
            self.event(db, task_id, 'discovered', '发现：已记录需求与' + ('固定验收契约' if real else '演示验收场景'))
            return task_id

    def action(self, task_id, action, actor='local-user', include_pr=False, plan_digest=None):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            state = row['state']
            if action == 'start':
                if state != 'pending':
                    raise Conflict('只有待处理工单可以启动')
                next_state, message = 'analyzing', '经理派工：进入分析阶段'
            elif action == 'approve':
                if state not in ('awaiting_release','done'):
                    raise Conflict('工单尚未进入待发布确认阶段')
                if not row['candidate_digest'] or row['verified_digest'] != row['candidate_digest']:
                    raise Conflict('候选产物与已验证版本不一致')
                if self.config.get('mode')=='codex':
                    plan=self.repositories.plan(row['project'],include_pr)|{'artifact_digest':row['candidate_digest']}
                    if include_pr and plan_digest!=digest(plan):
                        raise Conflict('发布目标已改变或缺少预览，请重新打开发布详情')
                    db.execute('UPDATE tasks SET approved_release_plan=? WHERE id=?',(encode(plan),task_id))
                db.execute('INSERT INTO approvals(task_id,artifact_digest,at,actor) VALUES(?,?,?,?)',
                           (task_id,row['candidate_digest'],utc(),actor))
                db.execute('UPDATE tasks SET approved_digest=candidate_digest WHERE id=?',(task_id,))
                next_state, message = 'releasing', (('已授权本地测试与 GitHub 草稿 PR 发布' if include_pr else '已授权同一验证版本发布到本地测试服务') if self.config.get('mode')=='codex' else '已确认此版本的模拟发布；不会部署到外部环境')
            elif action == 'stop':
                if state in ('done','stopped'):
                    raise Conflict('此工单已结束')
                next_state, message = 'stopped', '用户停止：保留已有记录和证据'
            elif action == 'reverify':
                if state not in ('blocked','awaiting_release') or not row['candidate_digest']:
                    raise Conflict('只有保留候选版本的阻塞或待发布工单可以复验')
                db.execute('UPDATE tasks SET verified_digest=NULL,approved_digest=NULL,approved_release_plan=NULL WHERE id=?',(task_id,))
                next_state,message='verifying','重新验证同一候选：旧验证和授权失效，保留完整历史'
            elif action == 'resume':
                if state!='blocked':
                    raise Conflict('只有异常阻塞阶段可以继续')
                last=db.execute('SELECT stage,output FROM runs WHERE task_id=? ORDER BY id DESC LIMIT 1',(task_id,)).fetchone()
                output=json.loads(last['output']) if last and last['output'] else {}
                if not output.get('error'):
                    raise Conflict('持续验收失败不能跳过修复上限；请检查后重新开始')
                next_state,message=last['stage'],'基础设施修复后，从失败阶段重试；保留已有证据与修复计数'
                db.execute('UPDATE tasks SET recoveries=0 WHERE id=?',(task_id,))
            elif action == 'retry':
                if state != 'blocked':
                    raise Conflict('只有阻塞工单可以手动重新开始')
                db.execute('''UPDATE tasks SET repair_count=0,recoveries=0,candidate_digest=NULL,
                           verified_digest=NULL,approved_digest=NULL,approved_release_plan=NULL WHERE id=?''',(task_id,))
                next_state, message = 'analyzing', '用户手动重新开始：创建新一轮有界执行'
            else:
                raise ValueError('未知操作')
            db.execute('DELETE FROM project_locks WHERE token=?',(row['lease_token'],))
            db.execute("UPDATE runs SET status='cancelled',finished_at=? WHERE token=? AND status='running'",
                       (utc(),row['lease_token']))
            db.execute('UPDATE tasks SET state=?,updated_at=?,lease_token=NULL,lease_until=NULL WHERE id=?',
                       (next_state,utc(),task_id))
            self.event(db, task_id, action, message)

    def claim(self):
        now = time.time()
        with self.transaction() as db:
            expired = db.execute('SELECT * FROM tasks WHERE lease_token IS NOT NULL AND lease_until<=?',(now,)).fetchall()
            for row in expired:
                db.execute("UPDATE runs SET status='expired',finished_at=? WHERE token=? AND status='running'",(utc(),row['lease_token']))
                recoveries = row['recoveries'] + 1
                state = 'blocked' if recoveries > self.config['max_recoveries'] else row['state']
                db.execute('UPDATE tasks SET recoveries=?,state=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?',
                           (recoveries,state,utc(),row['id']))
                self.event(db,row['id'],'lease_expired','执行租约超时；' + ('超过恢复上限，已阻塞' if state == 'blocked' else '等待重新领取'))
            db.execute('DELETE FROM project_locks WHERE lease_until<=?',(now,))
            marks = ','.join('?' for _ in RUNNABLE)
            row = db.execute(f'''SELECT * FROM tasks t WHERE state IN ({marks}) AND lease_token IS NULL
                AND NOT EXISTS(SELECT 1 FROM project_locks l WHERE l.project=t.project)
                ORDER BY priority,id LIMIT 1''',RUNNABLE).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            until = now + self.config['lease_seconds']
            db.execute('INSERT INTO project_locks(project,token,lease_until) VALUES(?,?,?)',(row['project'],token,until))
            db.execute('UPDATE tasks SET lease_token=?,lease_until=? WHERE id=?',(token,until,row['id']))
            real=self.config.get('mode')=='codex'
            run_id = db.execute('INSERT INTO runs(task_id,stage,role,token,started_at,status,simulated) VALUES(?,?,?,?,?,?,?)',
                (row['id'],row['state'],ROLE[row['state']],token,utc(),'running',0 if real else 1)).lastrowid
            self.event(db,row['id'],'claimed',f"{ROLE[row['state']]}已领取：{LABELS[row['state']]}（{'真实 Codex' if real else '模拟'}）")
            return dict(row) | {'lease_token':token, 'lease_until':until, 'run_id':run_id}

    def heartbeat(self, token):
        now = time.time()
        with self.transaction() as db:
            until = now + self.config['lease_seconds']
            changed = db.execute('UPDATE tasks SET lease_until=? WHERE lease_token=? AND lease_until>?',
                                 (until,token,now)).rowcount
            if changed:
                db.execute('UPDATE project_locks SET lease_until=? WHERE token=?',(until,token))
            return bool(changed)

    def finish(self, job, result):
        """Commit only if this exact attempt still owns a live lease."""
        token = job['lease_token']
        real=self.config.get('mode')=='codex'
        if not isinstance(result, dict) or result.get('simulated') is not (not real):
            raise ValueError('执行结果与数据库模式不匹配')
        if real and not result.get('error'):
            evidence=result.get('evidence',[])
            if not evidence or any(not r.get('thread_id') or r.get('exit_code')!=0 for r in evidence):
                raise ValueError('真实执行结果缺少 Codex 完成会话证据')
        with self.transaction() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?',(job['id'],)).fetchone()
            if not row or row['lease_token'] != token or row['lease_until'] <= time.time() or row['state'] != job['state']:
                return False
            state = row['state']
            repairs = row['repair_count']
            if result.get('error'):
                next_state, message = 'blocked', '执行异常：已阻塞，等待人工检查'
            elif state == 'analyzing':
                next_state, message = 'developing', '分析完成：已生成范围与验收条件' + ('' if real else '（模拟）')
            elif state == 'developing':
                candidate = result.get('artifact_digest') if real else digest(result)
                if not isinstance(candidate,str) or not re.fullmatch('[0-9a-f]{64}',candidate):
                    raise ValueError('候选产物摘要无效')
                db.execute('UPDATE tasks SET candidate_digest=?,verified_digest=NULL,approved_digest=NULL,approved_release_plan=NULL WHERE id=?',
                           (candidate,row['id']))
                next_state, message = 'verifying', '候选产物已登记：交给独立验证员' + ('' if real else '（模拟）')
            elif state == 'verifying':
                if not row['candidate_digest'] or result.get('artifact_digest') != row['candidate_digest']:
                    raise Conflict('验证结果没有绑定当前候选产物')
                if result.get('passed') is True:
                    if real:
                        checks=result.get('checks',{})
                        if not (checks.get('passed') is True and checks.get('compile',{}).get('exit_code')==0
                                and checks.get('tests',{}).get('exit_code')==0 and checks.get('total')==20
                                and checks.get('passed_count')==20 and checks.get('failed_count')==0
                                and re.fullmatch('[0-9a-f]{64}',checks.get('jar_sha256',''))
                                and checks.get('input_attestation',{}).get('candidate_digest')==row['candidate_digest']
                                and checks['input_attestation'].get('candidate_commit')==result.get('commit')
                                and checks['input_attestation'].get('jar_sha256')==checks['jar_sha256']):
                            raise Conflict('通过结论缺少真实编译、验收和二进制摘要证据')
                    db.execute('UPDATE tasks SET verified_digest=candidate_digest WHERE id=?',(row['id'],))
                    next_state, message = 'awaiting_release', '验证通过：等待同一版本的发布授权' + ('' if real else '（模拟）')
                elif repairs < self.config['max_repairs']:
                    repairs += 1
                    db.execute('UPDATE tasks SET candidate_digest=NULL,verified_digest=NULL,approved_digest=NULL,approved_release_plan=NULL WHERE id=?',(row['id'],))
                    next_state, message = 'developing', f'验证失败：退回修复，第 {repairs} / {self.config["max_repairs"]} 轮' + ('' if real else '（模拟）')
                else:
                    next_state, message = 'blocked', '验证持续失败：已达到自动修复上限'
            elif state == 'releasing':
                if not row['candidate_digest'] or row['approved_digest'] != row['candidate_digest'] or row['verified_digest'] != row['candidate_digest']:
                    raise Conflict('发布版本缺少对应的验证或确认')
                if result.get('artifact_digest') != row['candidate_digest']:
                    raise Conflict('发布结果版本不一致')
                if real:
                    verified=db.execute("SELECT output FROM runs WHERE task_id=? AND stage='verifying' AND status='completed' ORDER BY id DESC LIMIT 1",(row['id'],)).fetchone()
                    proof=json.loads(verified['output']) if verified else {}
                    manifest=result.get('release',{}).get('manifest',{})
                    if manifest.get('files',{}).get('candidate.jar')!=proof.get('checks',{}).get('jar_sha256') or manifest.get('commit')!=proof.get('commit'):
                        raise Conflict('发布二进制与已验证版本不一致')
                    plan=json.loads(row['approved_release_plan']) if row['approved_release_plan'] else {}
                    pr=result.get('release',{}).get('pull_request',{})
                    if plan.get('github_pr') and (pr.get('status')!='published' or pr.get('head_sha')!=proof.get('commit')
                        or pr.get('repository')!=plan['github_pr']['github_repository'] or pr.get('base')!=plan['github_pr']['base_branch']):
                        raise Conflict('GitHub PR 未确认或与授权的版本/仓库不一致')
                next_state, message = 'observing', ('已发布到本地测试服务：进入 HTTP 反馈验证' if real else '模拟发布完成：进入反馈观察；无外部部署')
            else:
                if real and (result.get('passed') is not True or result.get('artifact_digest')!=row['candidate_digest']):
                    next_state,message='blocked','发布后观察未通过：已阻塞，不能归档为成功'
                else:
                    next_state, message = 'done', '反馈检查完成：归档工单，并记录经验候选' + ('' if real else '（模拟）')
                    db.execute('INSERT INTO lessons(task_id,at,content) VALUES(?,?,?)',
                               (row['id'],utc(),'候选经验：发布前将验证证据、确认记录与同一产物摘要绑定；需人工复核后采用。'))
            db.execute('UPDATE runs SET status=?,finished_at=?,output=? WHERE token=?',
                ('failed' if next_state == 'blocked' or result.get('passed') is False else 'completed',utc(),encode(result),token))
            db.execute('UPDATE tasks SET state=?,repair_count=?,updated_at=?,lease_token=NULL,lease_until=NULL WHERE id=?',
                       (next_state,repairs,utc(),row['id']))
            db.execute('DELETE FROM project_locks WHERE token=?',(token,))
            self.event(db,row['id'],'transition',message)
            return True

    def guarded(self, job, operation):
        """Fence short local publication effects with the same transaction as stop/claim."""
        with self.transaction() as db:
            row=db.execute('SELECT state,lease_token,lease_until FROM tasks WHERE id=?',(job['id'],)).fetchone()
            if not row or row['lease_token']!=job['lease_token'] or row['state']!=job['state'] or row['lease_until']<=time.time():
                raise Conflict('Execution lease lost before publication')
            return operation()

    def progress(self, job, message):
        with self.transaction() as db:
            row=db.execute('SELECT lease_token,lease_until FROM tasks WHERE id=?',(job['id'],)).fetchone()
            if row and row['lease_token']==job['lease_token'] and row['lease_until']>time.time():
                self.event(db,job['id'],'role_progress',message)

    def record_publication(self,job,receipt):
        """Record a verified external side effect even if a stop raced with its response."""
        with self.transaction() as db:
            changed=db.execute('INSERT OR IGNORE INTO publications(identity,task_id,artifact_digest,receipt,at) VALUES(?,?,?,?,?)',
                (receipt['identity'],job['id'],job['candidate_digest'],encode(receipt),utc())).rowcount
            if changed:
                self.event(db,job['id'],'github_pr',f'GitHub 草稿 PR 已创建并核对版本：{receipt["url"]}')

    @staticmethod
    def public_task(row):
        fields = ('id','title','project','state','priority','repair_count','created_at','updated_at')
        value = {key:row[key] for key in fields}
        value['label'] = LABELS[row['state']]
        value['stage'] = 'active' if row['state'] in RUNNABLE else ('pending' if row['state'] in ('pending','awaiting_release','blocked') else row['state'])
        value['role'] = ROLE.get(row['state'], '项目经理')
        return value

    def snapshot(self):
        with self.connect() as db:
            db.execute('BEGIN')
            tasks = [self.public_task(row) for row in db.execute('SELECT * FROM tasks ORDER BY priority,id DESC')]
            events = [dict(row) for row in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 60')]
            lessons = db.execute('SELECT count(*) FROM lessons').fetchone()[0]
            completed=[json.loads(r['output']) for r in db.execute("SELECT output FROM runs WHERE simulated=0 AND status IN ('completed','failed') AND output IS NOT NULL ORDER BY id")]
            db.commit()
        counts = {key:sum(t['stage'] == key for t in tasks) for key in ('active','pending','done','stopped')}
        counts.update(resident=len(self.config['resident_roles']), blocked=sum(t['state']=='blocked' for t in tasks),
                      awaiting_release=sum(t['state']=='awaiting_release' for t in tasks), total=len(tasks))
        models=json.loads((BASE/'models.json').read_text(encoding='utf-8'))
        roles=[r|models['roles'].get(r['id'],{}) for r in self.config['resident_roles']]
        verification=[r for r in completed if r.get('stage')=='verifying' and 'checks' in r]
        releases=[r['release'] for r in completed if 'release' in r]
        execution={'codex_calls':sum(len(r.get('evidence',[])) for r in completed),
            'last_checks':{k:verification[-1]['checks'][k] for k in ('total','passed_count','failed_count')} if verification else None,
            'release_url':releases[-1]['url'] if releases else None,
            'pull_request':releases[-1].get('pull_request') if releases else None}
        repositories=self.repositories.list()
        for task in tasks:
            repository=next((r for r in repositories if r['id']==task['project']),None)
            task['repository_name']=repository['name'] if repository else task['project']
        return {'schema':1,'mode':self.config.get('mode','demo'),'revision':events[0]['id'] if events else 0,
                'content_updated_at':events[0]['at'] if events else None,
                'summary':counts,'resident_roles':roles,'items':tasks,'timeline':events,'models':models,'execution':execution,'repositories':repositories,
                'lesson_candidates':lessons,'policy':{'max_repairs':self.config['max_repairs'],'workers':self.config['workers']}}

    def detail(self, task_id):
        with self.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            task = self.public_task(row) | {'description':row['description'],'scenario':row['scenario'],
                'candidate_digest':row['candidate_digest'],'verified_digest':row['verified_digest'],'approved_digest':row['approved_digest'],
                'approved_release_plan':json.loads(row['approved_release_plan']) if row['approved_release_plan'] else None}
            runs = []
            for run in db.execute('SELECT id,stage,role,started_at,finished_at,status,simulated,output FROM runs WHERE task_id=? ORDER BY id',(task_id,)):
                runs.append(dict(run) | {'output':json.loads(run['output']) if run['output'] else None})
            approvals = [dict(r) for r in db.execute('SELECT artifact_digest,at,actor FROM approvals WHERE task_id=?',(task_id,))]
            lessons = [dict(r) for r in db.execute('SELECT content,status,at FROM lessons WHERE task_id=?',(task_id,))]
            publications=[json.loads(r['receipt']) for r in db.execute('SELECT receipt FROM publications WHERE task_id=? ORDER BY at',(task_id,))]
            db.commit()
            return {'task':task,'runs':runs,'approvals':approvals,'lessons':lessons,'publications':publications}

    def release_preview(self,task_id):
        detail=self.detail(task_id)
        project=detail['task']['project']
        repository=self.repositories.get(project)
        if not repository:
            raise ValueError('工单没有关联的仓库')
        if repository.get('path'):
            repository=self.repositories.refresh(project)
        available=bool(repository.get('github_repository') and repository.get('base_branch'))
        plan=self.repositories.plan(project,available)|{'artifact_digest':detail['task']['candidate_digest']}
        releases=[r['output']['release'] for r in detail['runs'] if r['output'] and 'release' in r['output']]
        return {'plan':plan,'plan_digest':digest(plan),'repository':repository,'task':detail['task'],
                'latest_release':releases[-1] if releases else None,'publications':detail['publications']}


class DemoRunner:
    """Adapter boundary. Never invokes a model, test suite, Git, or deployment service."""
    def run(self, job):
        stage = job['state']
        common = {'simulated':True,'stage':stage,'run_id':job['run_id']}
        if stage == 'analyzing':
            return common | {'scope':job['title'],'acceptance':['模拟候选生成','模拟验证通过','同版本确认与归档'],
                             'note':'这是一份预设分析结果，未调用大模型。'}
        if stage == 'developing':
            return common | {'candidate_revision':f"demo-{job['id']}-{job['run_id']}",
                             'artifact':{'title':job['title'],'repair_round':job['repair_count']},
                             'note':'已保存模拟候选记录；未编辑真实项目代码。'}
        if stage == 'verifying':
            passed = job['scenario'] == 'pass' or (job['scenario'] == 'repair_once' and job['repair_count'] > 0)
            return common | {'passed':passed,'artifact_digest':job['candidate_digest'],
                             'checks':[{'name':'预设演示验收','passed':passed}],
                             'note':'结果由演示场景决定；并非真实测试或独立模型审查。'}
        if stage == 'releasing':
            return common | {'artifact_digest':job['candidate_digest'],'environment':'simulation','external_deploy':False}
        return common | {'observation':'模拟健康检查通过','real_telemetry':False}


def worker(store, stop, runner=None, auto_release=False):
    runner = runner or DemoRunner()
    while not stop.is_set():
        if auto_release and store.config.get('mode')=='codex':
            for item in store.snapshot()['items']:
                if item['state']=='awaiting_release':
                    try:
                        store.action(item['id'],'approve',actor='explicit-local-test-policy')
                    except Conflict:
                        pass
        job = store.claim()
        if not job:
            stop.wait(.2)
            continue
        if stop.wait(store.config['step_seconds']):
            break
        try:
            result = runner.run(job)
            if job['state']=='observing' and result.get('passed') is False and hasattr(runner,'discard'):
                runner.discard(job)
            if not store.finish(job, result) and hasattr(runner,'discard'):
                runner.discard(job)
        except Exception as error:
            if hasattr(runner,'discard'):
                runner.discard(job)
            evidence=runner.failure_evidence(job) if hasattr(runner,'failure_evidence') else []
            store.finish(job, {'simulated':store.config.get('mode')!='codex','error':type(error).__name__,
                               'message':str(error)[:800],'evidence':evidence})
