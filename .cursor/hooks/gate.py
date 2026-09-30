#!/usr/bin/env python
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
event=sys.argv[1]

def deny(reason):
    print(json.dumps({'permission':'deny','user_message':reason,'agent_message':reason}))
    sys.exit(2)

raw=sys.stdin.buffer.read()
try:
    # Cursor on Windows may prefix the payload with a UTF-8 BOM.
    p=json.loads(raw.decode('utf-8-sig'))
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    print(f'cursor hook: unreadable payload ({len(raw)} bytes, head={raw[:32]!r}): {exc}', file=sys.stderr)
    deny('harness gate could not parse the Cursor hook payload')
r=subprocess.run([sys.executable,str(ROOT/'scripts/gate.py'),'hook','--event',event,'--provider','cursor'],input=json.dumps(p),text=True,encoding='utf-8',cwd=ROOT)
sys.exit(r.returncode)