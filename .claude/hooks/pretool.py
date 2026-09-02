#!/usr/bin/env python3
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
p=json.load(sys.stdin); tool=p.get('tool_name') or p.get('tool') or ''
event='pre-shell' if tool=='Bash' else 'pre-write' if tool in {'Edit','Write'} else None
if not event: sys.exit(0)
r=subprocess.run([sys.executable,str(ROOT/'scripts/gate.py'),'hook','--event',event,'--provider','claude'],input=json.dumps(p),text=True,cwd=ROOT)
sys.exit(r.returncode)
