"""Pinned Kotlin/JVM toolchain; trusted commands run outside model control."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from codex_client import Cancelled, terminate_tree

ARTIFACTS = [
    ('org.jetbrains.kotlin','kotlin-compiler-embeddable','2.0.21'),
    ('org.jetbrains.kotlin','kotlin-stdlib','2.0.21'),
    ('org.jetbrains.kotlin','kotlin-script-runtime','2.0.21'),
    ('org.jetbrains.kotlin','kotlin-reflect','1.6.10'),
    ('org.jetbrains.kotlin','kotlin-daemon-embeddable','2.0.21'),
    ('org.jetbrains.intellij.deps','trove4j','1.0.20200330'),
    ('org.jetbrains.kotlinx','kotlinx-coroutines-core-jvm','1.6.4'),
    ('org.jetbrains','annotations','13.0'),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    temporary.replace(path)


def command(argv, cwd, log, heartbeat=lambda:True, timeout=120, env=None):
    """No shell, bounded process tree, continuous lease renewal, durable log."""
    log=Path(log);log.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    if not heartbeat():
        raise Cancelled('Lease lost before command')
    with log.open('wb') as out:
        process=subprocess.Popen([str(x) for x in argv],cwd=str(cwd),stdout=out,stderr=subprocess.STDOUT,env=os.environ|env if env else None,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')
        try:
            last=0
            while process.poll() is None:
                now=time.monotonic()
                if now-started>timeout:
                    raise TimeoutError('Trusted command timed out')
                if now-last>=2:
                    if not heartbeat():
                        raise Cancelled('Lease lost during command')
                    last=now
                time.sleep(.1)
        except BaseException:
            terminate_tree(process)
            raise
    return {'exit_code':process.returncode,'duration_seconds':round(time.monotonic()-started,2),
            'log':str(log.resolve()),'log_sha256':sha(log)}


class Kotlin:
    def __init__(self, cache, download=False):
        self.cache=Path(cache).resolve();self.cache.mkdir(parents=True,exist_ok=True)
        java_home=os.environ.get('JAVA_HOME')
        self.java=str(Path(java_home)/'bin'/('java.exe' if os.name=='nt' else 'java')) if java_home else shutil.which('java')
        if not self.java or not Path(self.java).is_file():
            raise RuntimeError('Set JAVA_HOME to JDK 17 or newer')
        version=subprocess.run([self.java,'-version'],capture_output=True,text=True,timeout=15)
        match=re.search(r'version "(\d+)',version.stderr+version.stdout)
        if version.returncode or not match or int(match[1])<17:
            raise RuntimeError('JDK 17+ required; use JAVA_HOME')
        self.jars={}
        lock=json.loads((Path(__file__).resolve().parent/'toolchain-lock.json').read_text(encoding='utf-8'))
        gradle=Path(os.environ.get('GRADLE_USER_HOME',str(Path.home()/'.gradle')))/'caches/modules-2/files-2.1'
        for group,name,ver in ARTIFACTS:
            filename=f'{name}-{ver}.jar'
            target=self.cache/filename
            if not target.is_file():
                matches=list((gradle/group/name/ver).glob('*/'+filename))
                if matches:
                    shutil.copy2(matches[0],target)
                elif download:
                    url=f'https://repo.maven.apache.org/maven2/{group.replace(".","/")}/{name}/{ver}/{filename}'
                    with urllib.request.urlopen(url,timeout=90) as response:
                        data=response.read()
                    if hashlib.sha256(data).hexdigest()!=lock[filename]:
                        raise RuntimeError('Maven artifact checksum mismatch')
                    target.write_bytes(data)
                else:
                    raise RuntimeError(f'Missing {filename}; run python install.py --download')
            if sha(target)!=lock[filename]:
                raise RuntimeError(f'Toolchain checksum mismatch: {filename}')
            self.jars[name]=str(target)
        self.stdlib=self.jars['kotlin-stdlib']
        write_json(self.cache/'manifest.json',{Path(p).name:sha(p) for p in self.jars.values()})

    def build_test(self, source, output, heartbeat=lambda:True):
        source=Path(source);output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
        jar=output/'candidate.jar'
        inputs=[source/'src/BatteryPolicy.kt',source/'acceptance/PolicyAcceptance.kt',source/'app/PolicyProbe.kt']
        compile_result=command([self.java,'-cp',os.pathsep.join(self.jars.values()),
            'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler','-no-stdlib','-no-reflect',
            '-classpath',os.pathsep.join([self.stdlib,self.jars['annotations']]),
            *inputs,'-d',jar],source,output/'compile.log',heartbeat)
        result={'compile':compile_result,'passed':False,'total':20,'passed_count':0,'failed_count':20}
        if compile_result['exit_code']==0:
            test_result=command([self.java,'-cp',os.pathsep.join([str(jar),self.stdlib]),
                'rsi.lab.PolicyAcceptanceKt'],source,output/'tests.log',heartbeat)
            text=(output/'tests.log').read_text(encoding='utf-8',errors='replace')
            match=re.search(r'RESULT total=(\d+) passed=(\d+) failed=(\d+)',text)
            result.update(tests=test_result,jar=str(jar),jar_sha256=sha(jar),test_output=text)
            if match:
                total,passed,failed=map(int,match.groups())
                result.update(total=total,passed_count=passed,failed_count=failed,
                              passed=test_result['exit_code']==0 and total==20 and passed==20 and failed==0)
        write_json(output/'checks.json',result)
        return result
