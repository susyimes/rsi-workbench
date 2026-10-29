"""Dependency doctor/bootstrap. Never installs credentials or edits user Codex settings."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from codex_client import find_codex
from engine import BASE
from toolchain import Kotlin


def main():
    parser=argparse.ArgumentParser(description='Check RSI prerequisites and cache the pinned Kotlin toolchain')
    parser.add_argument('--download',action='store_true',help='Download missing pinned JARs from Maven Central')
    parser.add_argument('--data',type=Path,default=BASE/'runtime/codex')
    args=parser.parse_args()
    if sys.version_info<(3,10):
        raise RuntimeError('Python 3.10+ required')
    if not shutil.which('git'):
        raise RuntimeError('Git required')
    codex=find_codex()
    version=subprocess.run([codex,'--version'],capture_output=True,text=True,timeout=15)
    auth=subprocess.run([codex,'login','status'],capture_output=True,text=True,timeout=15)
    logged_in=auth.returncode==0
    if not logged_in:
        raise RuntimeError('Run codex login interactively, then run this doctor again')
    kotlin=Kotlin(args.data/'execution/toolchain',download=args.download)
    print(json.dumps({'ready':True,'python':sys.version.split()[0],'git':shutil.which('git'),
        'codex':version.stdout.strip(),'codex_authenticated':logged_in,'java':kotlin.java,
        'kotlin':'2.0.21','models_file':str(BASE/'models.json'),
        'note':'Model access is verified by actual role calls during the real example; no model call during install.'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
