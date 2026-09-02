#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, hashlib, json, os, platform, subprocess, tempfile
from datetime import datetime, timezone
from pathlib import Path
from harnesslib import ROOT, run_dir, sha256_file, write_json_atomic, git
from evidence import append, head_hash, validate

def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def fhash(p): return sha256_file(p) if p.exists() and p.is_file() else None
def gitval(*args):
    cp=git(*args,check=False); return cp.stdout.strip() if cp.returncode==0 else None

def canonical(payload): return json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def derive_public(private,out):
    cp=subprocess.run(['openssl','pkey','-in',str(private),'-pubout','-out',str(out)],capture_output=True,text=True)
    if cp.returncode: raise ValueError('openssl could not derive Ed25519 public key: '+cp.stderr.strip())
def sign_bytes(data,key):
    with tempfile.TemporaryDirectory() as td:
        src=Path(td)/'payload'; sig=Path(td)/'sig'; src.write_bytes(data)
        cp=subprocess.run(['openssl','pkeyutl','-sign','-rawin','-inkey',str(key),'-in',str(src),'-out',str(sig)],capture_output=True,text=True)
        if cp.returncode: raise ValueError('openssl signing failed: '+cp.stderr.strip())
        return sig.read_bytes()
def verify_bytes(data,sig,key):
    with tempfile.TemporaryDirectory() as td:
        src=Path(td)/'payload'; sf=Path(td)/'sig'; src.write_bytes(data); sf.write_bytes(sig)
        cp=subprocess.run(['openssl','pkeyutl','-verify','-pubin','-rawin','-inkey',str(key),'-in',str(src),'-sigfile',str(sf)],capture_output=True,text=True)
        return cp.returncode==0

def make_payload(task,risk):
    v=validate(task)
    if not v['valid']: raise ValueError('evidence ledger invalid: '+v['reason'])
    rd=run_dir(task)
    return {'schema_version':1,'task_id':task,'risk':risk,'created_at':now(),'git_head':gitval('rev-parse','HEAD'),'git_tree':gitval('write-tree'),'manifest_sha256':fhash(ROOT/'harness/manifest.yaml'),'route_sha256':fhash(rd/'route.json'),'context_sha256':fhash(rd/'context.json'),'model_selections_sha256':fhash(rd/'model-selections.json'),'evidence_head_hash':head_hash(task),'python_version':platform.python_version()}

def create(task,risk,key,output=None):
    key=Path(key).expanduser().resolve()
    try: key.relative_to(ROOT.resolve()); raise ValueError('private attestation key must not be stored inside the repository')
    except ValueError as e:
        if 'must not' in str(e): raise
    payload=make_payload(task,risk); sig=sign_bytes(canonical(payload),key)
    with tempfile.TemporaryDirectory() as td:
        pub=Path(td)/'pub.pem'; derive_public(key,pub); pub_der=subprocess.run(['openssl','pkey','-pubin','-in',str(pub),'-outform','DER'],capture_output=True).stdout
        fingerprint=hashlib.sha256(pub_der).hexdigest()
    doc={'payload':payload,'signature':{'algorithm':'Ed25519','value_base64':base64.b64encode(sig).decode(),'public_key_sha256':fingerprint}}
    dest=Path(output) if output else run_dir(task)/'attestation.json'; dest=dest if dest.is_absolute() else ROOT/dest; write_json_atomic(dest,doc)
    append(task,'attestation','DETERMINISTIC','Candidate provenance signed with Ed25519','PASS','ci-attestor',artifact=dest.relative_to(ROOT).as_posix() if dest.is_relative_to(ROOT) else str(dest),notes=f'public_key_sha256={fingerprint}; evidence_head_before_attestation={payload["evidence_head_hash"]}')
    return dest,doc

def verify(path,key):
    doc=json.loads(Path(path).read_text(encoding='utf-8')); sig=base64.b64decode(doc['signature']['value_base64']); return verify_bytes(canonical(doc['payload']),sig,Path(key).expanduser())

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('create'); p.add_argument('task'); p.add_argument('--risk',required=True,choices=['R0','R1','R2','R3']); p.add_argument('--private-key',default=os.getenv('HARNESS_ATTESTATION_PRIVATE_KEY')); p.add_argument('--output')
    p=sub.add_parser('verify'); p.add_argument('file'); p.add_argument('--public-key',default=os.getenv('HARNESS_ATTESTATION_PUBLIC_KEY'))
    a=ap.parse_args()
    try:
        if a.cmd=='create':
            if not a.private_key: raise ValueError('private key required via --private-key or HARNESS_ATTESTATION_PRIVATE_KEY')
            dest,doc=create(a.task,a.risk,a.private_key,a.output); print(json.dumps({'ok':True,'output':str(dest),'public_key_sha256':doc['signature']['public_key_sha256']},indent=2))
        else:
            if not a.public_key: raise ValueError('public key required via --public-key or HARNESS_ATTESTATION_PUBLIC_KEY')
            ok=verify(a.file,a.public_key); print(json.dumps({'ok':ok},indent=2)); raise SystemExit(0 if ok else 2)
    except Exception as e: print(json.dumps({'ok':False,'reason':str(e)},ensure_ascii=False,indent=2)); raise SystemExit(2)
if __name__=='__main__': main()
