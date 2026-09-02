#!/usr/bin/env python3
import json,sys
case=json.load(open(sys.argv[1])); out={'passed':case.get('force_fail') is not True,'tokens':100,'cost_usd':0.01,'latency_seconds':0.1,'unsafe_action_attempts':0,'human_interventions':0}
json.dump(out,open(sys.argv[2],'w'))
