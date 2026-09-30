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

def record_session(payload):
    """Expose the Cursor chat id as the provider session for review consent."""
    sid=payload.get('conversation_id') if isinstance(payload,dict) else None
    if not isinstance(sid,str) or not sid:
        return
    try:
        sys.path.insert(0,str(ROOT/'scripts'))
        from datetime import datetime, timezone
        from harnesslib import provider_session_path, write_json_atomic
        path=provider_session_path('cursor')
        if path.is_file() and json.loads(path.read_text(encoding='utf-8')).get('session_id')==sid:
            return
        write_json_atomic(path,{'schema_version':1,'session_id':sid,'observed_at':datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        print(f'cursor hook: session not recorded: {exc}', file=sys.stderr)

record_session(p)
r=subprocess.run([sys.executable,str(ROOT/'scripts/gate.py'),'hook','--event',event,'--provider','cursor'],input=json.dumps(p),text=True,encoding='utf-8',cwd=ROOT)
sys.exit(r.returncode)