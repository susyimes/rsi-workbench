"""Non-interactive Codex client with bounded runtime and cooperative cancellation."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


class Cancelled(RuntimeError):
    pass


def find_codex():
    override = os.environ.get('RSI_CODEX_EXE')
    if override:
        path = Path(override).resolve()
        if not path.is_file() or path.suffix.lower() in ('.cmd','.ps1','.bat'):
            raise RuntimeError('RSI_CODEX_EXE must point to a native Codex executable')
        return str(path)
    native = shutil.which('codex.exe' if os.name == 'nt' else 'codex')
    if native:
        return native
    if os.name == 'nt':
        npm_root = Path(os.environ.get('APPDATA','')) / 'npm/node_modules/@openai/codex'
        matches = list(npm_root.glob('node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe'))
        if len(matches) == 1:
            return str(matches[0])
    raise RuntimeError('Native Codex executable not found; set RSI_CODEX_EXE')


def terminate_tree(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15)
    else:
        import signal
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=15)


ROLE_SCHEMA = {
    'type':'object', 'additionalProperties':False,
    'properties':{
        'summary':{'type':'string'},
        'decision':{'type':'string','enum':['ready','passed','failed','blocked']},
        'findings':{'type':'array','items':{'type':'string'}},
        'changed_files':{'type':'array','items':{'type':'string'}},
        'limitations':{'type':'array','items':{'type':'string'}},
    },
    'required':['summary','decision','findings','changed_files','limitations']
}
CODE_SCHEMA = ROLE_SCHEMA | {
    'properties':ROLE_SCHEMA['properties']|{'replacement':{
        'type':'object','additionalProperties':False,
        'properties':{'path':{'type':'string','enum':['src/BatteryPolicy.kt']},
                      'base_sha256':{'type':'string'},'content':{'type':'string'}},
        'required':['path','base_sha256','content']}},
    'required':ROLE_SCHEMA['required']+['replacement']}


class CodexClient:
    def __init__(self, role_config, timeout=420):
        self.roles = role_config
        self.timeout = timeout
        self.executable = find_codex()

    def run(self, role, prompt, cwd, evidence_dir, heartbeat=lambda: True, writable=False, schema=None):
        evidence_dir = Path(evidence_dir).resolve()
        evidence_dir.mkdir(parents=True,exist_ok=True)
        spec = self.roles[role]
        schema_path = evidence_dir / 'response.schema.json'
        output_path = evidence_dir / 'response.json'
        schema=schema or ROLE_SCHEMA
        schema_path.write_text(json.dumps(schema),encoding='utf-8')
        (evidence_dir / 'prompt.txt').write_text(prompt,encoding='utf-8')
        argv = [self.executable,'exec','--ignore-user-config','--ephemeral',
                '--sandbox','workspace-write' if writable else 'read-only',
                '--cd',str(Path(cwd).resolve()),'--model',spec['model'],
                '-c',f'model_reasoning_effort="{spec["effort"]}"',
                '-c','approval_policy="never"','--json','--color','never',
                '--output-schema',str(schema_path),'--output-last-message',str(output_path),'-']
        env = {k:v for k,v in os.environ.items() if k.upper() in
               {'SYSTEMROOT','WINDIR','TEMP','TMP','PATH','USERPROFILE','APPDATA','LOCALAPPDATA',
                'HOME','JAVA_HOME','CODEX_HOME','PROGRAMFILES','PROGRAMFILES(X86)','COMSPEC','PATHEXT'}}
        env['PYTHONIOENCODING']='utf-8'
        started=time.time()
        with (evidence_dir/'events.jsonl').open('wb') as out, (evidence_dir/'stderr.log').open('wb') as err:
            process=subprocess.Popen(argv,cwd=str(cwd),stdin=subprocess.PIPE,stdout=out,stderr=err,env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                start_new_session=os.name!='nt')
            try:
                process.stdin.write(prompt.encode('utf-8'));process.stdin.close()
                last_heartbeat=0
                while process.poll() is None:
                    now=time.time()
                    if now-started>self.timeout:
                        raise TimeoutError(f'Codex {role} exceeded {self.timeout}s')
                    if now-last_heartbeat>=2:
                        if not heartbeat():
                            raise Cancelled('Run stopped or lease lost')
                        last_heartbeat=now
                    time.sleep(.2)
            except BaseException:
                terminate_tree(process)
                raise
        if process.returncode or not output_path.is_file():
            raise RuntimeError(f'Codex {role} failed with exit code {process.returncode}; inspect private run logs')
        value=json.loads(output_path.read_text(encoding='utf-8-sig'))
        if set(value)!=set(schema['required']) or value['decision'] not in ('ready','passed','failed','blocked'):
            raise RuntimeError('Invalid Codex structured output')
        if not isinstance(value['summary'],str) or any(not isinstance(value[k],list) or any(not isinstance(x,str) for x in value[k]) for k in ('findings','changed_files','limitations')):
            raise RuntimeError('Invalid Codex field types')
        if 'replacement' in value:
            patch=value['replacement']
            if not isinstance(patch,dict) or set(patch)!={'path','base_sha256','content'} or patch['path']!='src/BatteryPolicy.kt' or any(not isinstance(v,str) for v in patch.values()) or len(patch['content'])>50000:
                raise RuntimeError('Invalid replacement contract')
        thread_id=None;usage=None;completed=False
        for line in (evidence_dir/'events.jsonl').read_text(encoding='utf-8',errors='replace').splitlines():
            try:
                event=json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('type')=='thread.started':
                thread_id=event.get('thread_id')
            if event.get('type')=='turn.completed':
                completed=True;usage=event.get('usage')
        if not completed or not thread_id:
            raise RuntimeError('Codex completion event or session ID missing')
        result={'role':role,'model':spec['model'],'effort':spec['effort'],'thread_id':thread_id,
                'exit_code':process.returncode,'duration_seconds':round(time.time()-started,2),
                'usage':usage,'response':value,'evidence_dir':str(evidence_dir)}
        (evidence_dir/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
