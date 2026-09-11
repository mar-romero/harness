#!/usr/bin/env python
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
p=json.load(sys.stdin); event=sys.argv[1]
r=subprocess.run([sys.executable,str(ROOT/'scripts/gate.py'),'hook','--event',event,'--provider','cursor'],input=json.dumps(p),text=True,cwd=ROOT)
sys.exit(r.returncode)
