#!/usr/bin/env python
"""Inspect the OpenCode runtime inventory produced by the harness plugin."""
from __future__ import annotations
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / '.harness/opencode/model-inventory.json'
OVERRIDES = ROOT / 'harness/opencode-model-overrides.json'


def main() -> int:
    ap=argparse.ArgumentParser(description='Inspect OpenCode model inventory/profile coverage.')
    ap.add_argument('--json', action='store_true', help='Emit machine-readable summary')
    args=ap.parse_args()
    if not INVENTORY.exists():
        raise SystemExit('OpenCode inventory not found. Start OpenCode in this project so .opencode/plugins/harness can export the runtime catalog.')
    inv=json.loads(INVENTORY.read_text(encoding='utf-8'))
    profiles=json.loads(OVERRIDES.read_text(encoding='utf-8')).get('profiles',{}) if OVERRIDES.exists() else {}
    rows=[]
    for m in inv.get('models',[]):
        base=m.get('id','').split('#',1)[0]
        c=m.get('capabilities',{})
        reviewed=base in profiles
        rows.append({'id':m.get('id'),'reviewed_profile':reviewed,'reasoning':c.get('reasoning',0),'coding':c.get('coding',0),'tool_use':c.get('tool_use',0),'reliability':c.get('reliability',0),'cost':m.get('cost',0),'latency':m.get('latency',0)})
    out={'generated_at':inv.get('generated_at'),'models':len(rows),'reviewed_profiles':sum(1 for r in rows if r['reviewed_profile']),'unreviewed':[r['id'] for r in rows if not r['reviewed_profile']], 'rows':rows}
    if args.json:
        print(json.dumps(out,indent=2,ensure_ascii=False)); return 0
    print(f"OpenCode inventory: {out['models']} models; reviewed profiles: {out['reviewed_profiles']}")
    if out['unreviewed']:
        print('Models using conservative discovery defaults (add reviewed scores only when justified):')
        for model in out['unreviewed']: print(' -',model)
    return 0

if __name__=='__main__': raise SystemExit(main())
