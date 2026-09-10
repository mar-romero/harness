#!/usr/bin/env python
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from harnesslib import ROOT, write_json_atomic

URL_RE=re.compile(r'https?://[^\s)\]}>]+')
NUM_RE=re.compile(r'(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?:%|ms|s|m|h|kb|mb|gb)?',re.I)
PATH_RE=re.compile(r'(?<![\w-])(?:\.?\.?/)?(?:[\w.-]+/)+[\w.*{}@+-]+')
QUOTED_RE=re.compile(r'(["\'`])(.+?)\1')
CODE_RE=re.compile(r'```[\s\S]*?```|`[^`\n]+`')
IDENT_RE=re.compile(r'\b[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.|-)[A-Za-z0-9_]+)+\b')
NEG_ORIG=re.compile(r'\b(no|nunca|jam[aá]s|sin|evitar|prohibid[oa]|neither|not|never|without|avoid|forbid|must not|do not|don\'t)\b',re.I)
NEG_EN=re.compile(r'\b(not|never|without|avoid|forbid|forbidden|must not|do not|don\'t|cannot|can\'t)\b',re.I)
STRICT_ORIG=re.compile(r'\b(debe|deber[aá]|obligatori[oa]|solamente|s[oó]lo|exactamente|must|required|only|exactly)\b',re.I)
STRICT_EN=re.compile(r'\b(must|required|only|exactly|shall|need(?:s)? to)\b',re.I)

def extract(pattern,text): return sorted(set(m.group(0) if hasattr(m,'group') else m for m in pattern.finditer(text)))
def invariants(text):
    return {
      'code_blocks': extract(CODE_RE,text), 'urls': extract(URL_RE,text), 'file_paths': extract(PATH_RE,text),
      'numbers': extract(NUM_RE,text), 'identifiers': extract(IDENT_RE,text),
      'quoted_literals': sorted(set(m.group(2) for m in QUOTED_RE.finditer(text))),
      'has_negation': bool(NEG_ORIG.search(text)), 'has_strict_constraint': bool(STRICT_ORIG.search(text))
    }

def validate(original, english, language, back_translation=None, equivalence=''):
    issues=[]; src=invariants(original); dst=invariants(english)
    for key in ('code_blocks','urls','file_paths','numbers','identifiers','quoted_literals'):
        missing=[x for x in src[key] if x not in dst[key]]
        if missing: issues.append({'kind':'semantic_invariant_missing','field':key,'values':missing})
    if src['has_negation'] and not NEG_EN.search(english): issues.append({'kind':'negation_not_preserved'})
    if src['has_strict_constraint'] and not STRICT_EN.search(english): issues.append({'kind':'strict_constraint_not_preserved'})
    non_en=language.lower() not in {'en','eng','english'}
    if non_en and not back_translation: issues.append({'kind':'back_translation_required'})
    if non_en and equivalence != 'EXACT_INTENT': issues.append({'kind':'exact_intent_attestation_required'})
    if not english.strip(): issues.append({'kind':'canonical_english_empty'})
    return {'ok':not issues,'issues':issues,'source_invariants':src,'canonical_invariants':dst}

def normalize_task(task, *, english=None, language=None, back_translation=None, equivalence=None):
    original=(task.get('request') or {}).get('original_text') or task.get('description','')
    lang=language or (task.get('request') or {}).get('original_language') or 'unknown'
    existing=(task.get('request') or {}).get('canonical_english')
    canonical=english or existing or (original if lang.lower() in {'en','eng','english'} else '')
    eq=equivalence or ((task.get('request') or {}).get('translation') or {}).get('equivalence_claim','')
    back=back_translation or ((task.get('request') or {}).get('translation') or {}).get('back_translation')
    result=validate(original,canonical,lang,back,eq if lang.lower() not in {'en','eng','english'} else 'EXACT_INTENT')
    if not result['ok']:
        raise ValueError('request translation failed closed: '+json.dumps(result['issues'],ensure_ascii=False))
    out=dict(task); out['description']=canonical
    out['request']={
      'original_text':original,'original_language':lang,'canonical_english':canonical,
      'translation':{'equivalence_claim':'EXACT_INTENT','back_translation':back,'validation':result}
    }
    return out

def main():
    ap=argparse.ArgumentParser(description='Create a canonical English task while preserving and validating the original request.')
    ap.add_argument('task'); ap.add_argument('--english'); ap.add_argument('--language'); ap.add_argument('--back-translation'); ap.add_argument('--equivalence',choices=['EXACT_INTENT']); ap.add_argument('--output')
    a=ap.parse_args(); p=Path(a.task); task=json.loads(p.read_text(encoding='utf-8'))
    try: out=normalize_task(task,english=a.english,language=a.language,back_translation=a.back_translation,equivalence=a.equivalence)
    except ValueError as e: print(json.dumps({'ok':False,'reason':str(e)},ensure_ascii=False,indent=2)); raise SystemExit(2)
    dest=Path(a.output) if a.output else p
    if not dest.is_absolute(): dest=ROOT/dest
    write_json_atomic(dest,out); print(json.dumps({'ok':True,'output':str(dest),'task_id':out.get('id'),'canonical_english':out['description']},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
