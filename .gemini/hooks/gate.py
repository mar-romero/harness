#!/usr/bin/env python
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
payload=json.load(sys.stdin)
tool=payload.get('tool_name','')
event='pre-shell' if tool=='run_shell_command' else 'pre-write'
proc=subprocess.run(
    [sys.executable,str(ROOT/'scripts/gate.py'),'hook','--event',event,'--provider','gemini'],
    input=json.dumps(payload),text=True,capture_output=True,cwd=ROOT
)
sys.stdout.write(proc.stdout or '{}\n')
sys.stderr.write(proc.stderr)
raise SystemExit(proc.returncode)
