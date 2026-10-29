"""Loopback-only Codex workbench. Demo is available only as an explicit mode."""
import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from engine import BASE, CONFIG, Conflict, Store, digest, encode, worker


class Handler(BaseHTTPRequestHandler):
    server_version = 'RSI-Workbench/1.0'

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body=b'', mime='application/json; charset=utf-8', etag=None):
        if not isinstance(body, bytes):
            body = encode(body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        if etag:
            self.send_header('ETag', etag)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def valid_host(self):
        port = self.server.server_address[1]
        return self.headers.get('Host', '') in (f'127.0.0.1:{port}', f'localhost:{port}')

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error':'Host 不允许'})
        path = urlsplit(self.path).path
        if path == '/api/health':
            return self.send(200, {'ok':True,'mode':self.server.store.config.get('mode','demo')})
        if path == '/api/snapshot':
            value = self.server.store.snapshot()
            etag = '"' + digest(value) + '"'
            if self.headers.get('If-None-Match') == etag:
                return self.send(304, etag=etag)
            return self.send(200, value, etag=etag)
        if path == '/api/repositories':
            return self.send(200,{'items':self.server.store.repositories.list()})
        release_match=re.fullmatch(r'/api/tasks/(\d+)/release',path)
        if release_match:
            try:
                return self.send(200,self.server.store.release_preview(int(release_match[1])))
            except KeyError:
                return self.send(404,{'error':'工单不存在'})
            except ValueError as error:
                return self.send(400,{'error':str(error)})
        match = re.fullmatch(r'/api/tasks/(\d+)', path)
        if match:
            try:
                return self.send(200, self.server.store.detail(int(match[1])))
            except KeyError:
                return self.send(404, {'error':'工单不存在'})
        files = {'/':('index.html','text/html; charset=utf-8'),
                 '/app.js':('app.js','text/javascript; charset=utf-8'),
                 '/styles.css':('styles.css','text/css; charset=utf-8')}
        if path in files:
            name, mime = files[path]
            return self.send(200, (BASE / 'web' / name).read_bytes(), mime)
        self.send(404, {'error':'页面不存在'})

    def do_POST(self):
        port = self.server.server_address[1]
        origin = self.headers.get('Origin')
        allowed_origins = (f'http://127.0.0.1:{port}', f'http://localhost:{port}')
        if not self.valid_host() or (origin and origin not in allowed_origins) or self.headers.get('X-RSI-Client') != 'local-dashboard':
            return self.send(403, {'error':'仅允许本地工作台发起操作'})
        if self.headers.get('Content-Type','').split(';')[0].strip() != 'application/json':
            return self.send(415, {'error':'请求必须使用 application/json'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
                return self.send(413, {'error':'请求大小超出范围'})
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('请求必须是 JSON 对象')
            path = urlsplit(self.path).path
            if path == '/api/tasks':
                task_id = self.server.store.create(payload,self.headers.get('Idempotency-Key',''))
                return self.send(200, {'id':task_id})
            if path=='/api/repositories':
                if set(payload)-{'path','name'}:
                    raise ValueError('仓库仅接受路径和显示名称')
                return self.send(200,self.server.store.repositories.add(payload.get('path'),payload.get('name')))
            repository_match=re.fullmatch(r'/api/repositories/(repo-[a-f0-9]{12})/refresh',path)
            if repository_match:
                return self.send(200,self.server.store.repositories.refresh(repository_match[1]))
            match = re.fullmatch(r'/api/tasks/(\d+)/(start|approve|stop|retry|resume|reverify)', path)
            if match:
                if set(payload)-{'include_pr','plan_digest'} or type(payload.get('include_pr',False)) is not bool:
                    raise ValueError('发布参数无效')
                self.server.store.action(int(match[1]), match[2],include_pr=payload.get('include_pr',False),plan_digest=payload.get('plan_digest'))
                return self.send(200, {'ok':True})
            return self.send(404, {'error':'操作不存在'})
        except KeyError:
            self.send(404, {'error':'工单不存在'})
        except Conflict as error:
            self.send(409, {'error':str(error)})
        except (ValueError, UnicodeDecodeError) as error:
            self.send(400, {'error':str(error)})


def make_server(store, port):
    server = ThreadingHTTPServer(('127.0.0.1',port), Handler)
    server.daemon_threads = True
    server.store = store
    return server


def main():
    parser = argparse.ArgumentParser(description='RSI 自迭代工作台 · 真实 Codex / 独立测试仓库')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--mode', choices=('codex','demo'), default='codex')
    parser.add_argument('--run-example', action='store_true', help='创建并启动 permission policy 独立测试工单')
    parser.add_argument('--auto-release-local', action='store_true', help='预授权通过验证的同一版本发布到本地测试服务')
    parser.add_argument('--no-fault-injection', action='store_true', help='关闭首次候选通过后的故障注入')
    parser.add_argument('--seed', action='store_true', help='添加两张明确标注的演示工单')
    args = parser.parse_args()
    args.data=(args.data or BASE/'runtime'/args.mode).resolve()
    config=CONFIG|{'mode':args.mode,'step_seconds':.05 if args.mode=='codex' else CONFIG['step_seconds']}
    store = Store(args.data / 'rsi.sqlite3',config)
    stop = threading.Event()
    runner=None
    if args.mode=='codex':
        from codex_runner import CodexRunner
        runner=CodexRunner(store,args.data/'execution',stop,inject_fault=not args.no_fault_injection)
        runner.restore_releases()
    if args.seed:
        if args.mode!='demo':
            parser.error('--seed requires --mode demo')
        store.create({'title':'优化工作台的快照刷新体验','description':'演示正常完成分析、开发、验证、发布确认和反馈归档。','scenario':'pass'},'seed-normal-v1')
        store.create({'title':'修复移动端工单卡片布局','description':'演示首次验证失败，自动修复一次后重新验证。','scenario':'repair_once'},'seed-repair-v1')
    if args.run_example:
        if args.mode!='codex':
            parser.error('--run-example requires --mode codex')
        task_id=store.create({'title':'权限策略：独立开发、修复与发布验收',
            'description':'按固定 Kotlin/JVM 契约区分进入权限管理与主动授权；包含真实失败检测、修复及本地服务发布。',
            'project':'permission-policy-lab','scenario':'permission_policy'},'permission-policy-real-v1')
        if store.detail(task_id)['task']['state']=='pending':
            store.action(task_id,'start')
    server = make_server(store,args.port)
    threads = [threading.Thread(target=worker,args=(store,stop,runner,args.auto_release_local),daemon=True) for _ in range(store.config['workers'])]
    for thread in threads:
        thread.start()
    print(f'RSI {args.mode} ready: http://127.0.0.1:{server.server_address[1]}', flush=True)
    print('Real Codex and local test publication.' if args.mode=='codex' else 'Simulation only.',flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        for thread in threads:
            thread.join(timeout=8)
        if runner:
            runner.close()


if __name__ == '__main__':
    main()
