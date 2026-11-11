#!/usr/bin/env python3
"""Free candidate-source proof, not a production/paid preview activation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('repo',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--engine-source',type=Path,help='explicit local engine source for hermetic verification')
    a=p.parse_args(argv);root=a.repo.resolve();engine=(a.engine_source or root.parent/'interview-engine').resolve()
    if not (engine/'src/interview_engine/pg_checkpoint.py').is_file():
        raise ValueError('sibling interview-engine source is missing')
    for name in ('pytest','pgserver','psycopg','interview_engine','delivery_api'):
        __import__(name)
    a.output.mkdir(parents=True,exist_ok=True,mode=0o700)
    # No caller DSN, identity, API key or execution flag enters the test process.
    env={key:value for key,value in os.environ.items() if key in ('PATH','HOME','LANG','TMPDIR','SYSTEMROOT')}
    env.update(DELIVERY_TEST_ENGINE_SOURCE=str(engine),ANSWER_TRACE_DUMP='1',PYTHONUNBUFFERED='1')
    bootstrap="import sys;sys.path[:0]=sys.argv[1:3];import interview_engine,pytest;raise SystemExit(pytest.main(['-p','no:cacheprovider','tests/test_answer_host_integration.py','-k','success','-qs']))"
    result=subprocess.run([sys.executable,'-c',bootstrap,str(root/'src'),str(engine/'src')],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    totals={};traces=[]
    for line in result.stdout.splitlines():
        if line.startswith('ANSWER_TRACE_DUMP '):
            value,_=json.JSONDecoder().raw_decode(line.split(' ',1)[1]);traces.append(value)
        if line.startswith('ANSWER_TOTAL success '):totals['cold']=int(line.split()[2])
        if line.startswith('ANSWER_FOLLOWUP '):
            parts=line.split();totals['next_cold' if parts[1]=='0' else 'warm']=int(parts[2])
    summary={'kind':'free-fake-provider-disposable-pg-candidate-proof','estimate_credits':'0.1','live_provider_calls':0,
        'production_activation':False,'docker_verified':False,'live_pooler_verified':False,'pytest_exit':result.returncode,
        'barriers':totals,'limits':{'cold':29,'next_cold':29,'warm':26}}
    passed=result.returncode==0 and set(totals)=={'cold','next_cold','warm'} and all(totals[key]<=limit for key,limit in summary['limits'].items())
    summary['passed']=passed
    (a.output/'answer-proof.json').write_text(json.dumps(summary,indent=2)+'\n')
    # Trace metadata has no SQL, parameters, answer text or credentials.
    (a.output/'answer-traces.jsonl').write_text(''.join(json.dumps(value)+'\n' for value in traces))
    table='Outcome       Syncs  Limit\n'+''.join(f'{key:13} {totals.get(key,"MISSING")!s:5}  {limit}\n' for key,limit in summary['limits'].items())
    (a.output/'answer-sync-table.txt').write_text(table)
    print(table,end='')
    print(('PASS' if passed else 'FAIL')+': cold <=29 and warm <=26; free fake provider, disposable PG; not real Docker/pooler acceptance')
    if not passed:
        print('FAIL: test did not prove the candidate. No paid fallback and no provider retry. Test exit='+str(result.returncode))
    return 0 if passed else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('FAIL: free answer proof prerequisite or execution error ('+type(exc).__name__+'); no paid fallback',file=sys.stderr)
        raise SystemExit(2)
