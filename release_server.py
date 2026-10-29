"""Local test deployment of the exact verified Kotlin artifact."""
import argparse
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from toolchain import sha

FIELDS=('entry','xiaomiFamily','systemExempt','previouslyAuthorized','firstAuthorization',
        'ownVpnConnected','backgroundRestricted','oemUnrestricted')


def verify_release(directory):
    directory=Path(directory)
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    for name in ('candidate.jar','kotlin-stdlib.jar'):
        if sha(directory/name)!=manifest['files'][name]:
            raise ValueError('Release artifact changed')
    return manifest


def make_release(directory, java, port=0):
    directory=Path(directory).resolve()
    verify_release(directory)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, value):
            data=json.dumps(value,ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(data)

        def valid(self):
            port=self.server.server_address[1]
            return self.headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}')

        def do_GET(self):
            if not self.valid():
                return self.reply(403,{'error':'Host not allowed'})
            if self.path not in ('/','/health'):
                return self.reply(404,{'error':'Not found'})
            try:
                manifest=verify_release(directory)
                self.reply(200,{'ok':True,'environment':'local-test',
                    'commit':manifest['commit'],'artifact_digest':manifest['artifact_digest'],
                    'jar_sha256':manifest['files']['candidate.jar']})
            except Exception:
                self.reply(503,{'error':'Release integrity check failed'})

        def do_POST(self):
            if not self.valid():
                return self.reply(403,{'error':'Host not allowed'})
            if self.path!='/evaluate':
                return self.reply(404,{'error':'Not found'})
            try:
                verify_release(directory)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<4096:
                    raise ValueError('Invalid size')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict) or set(data)!=set(FIELDS) or data['entry'] not in ('MANAGE','AUTHORIZE'):
                    raise ValueError('Invalid fields')
                for field in FIELDS[1:]:
                    if data[field] is not None and type(data[field]) is not bool:
                        raise ValueError('Boolean or null required')
                for field in ('xiaomiFamily','previouslyAuthorized','firstAuthorization'):
                    if type(data[field]) is not bool:
                        raise ValueError('Required boolean is null')
                args=[str(data[k]).lower() if data[k] is not None else 'null' for k in FIELDS]
                args[0]=data['entry']
                result=subprocess.run([java,'-cp',os.pathsep.join([str(directory/'candidate.jar'),
                    str(directory/'kotlin-stdlib.jar')]),'rsi.lab.PolicyProbeKt',*args],
                    capture_output=True,text=True,encoding='utf-8',timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                if result.returncode:
                    return self.reply(500,{'error':'Policy process failed'})
                self.reply(200,json.loads(result.stdout))
            except (ValueError,KeyError,TypeError):
                self.reply(400,{'error':'Invalid policy input or changed artifact'})
            except Exception:
                self.reply(503,{'error':'Policy service unavailable'})

    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--java',default=str(Path(os.environ.get('JAVA_HOME',''))/'bin/java.exe') if os.name=='nt' else 'java')
    parser.add_argument('--port',type=int,default=8770)
    args=parser.parse_args()
    server=make_release(args.directory,args.java,args.port)
    print(f'Local policy service: http://127.0.0.1:{server.server_address[1]}/health',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
