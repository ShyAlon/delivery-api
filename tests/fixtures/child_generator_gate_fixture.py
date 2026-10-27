"""Hermetic gate-only CLI substitute; production child launch remains unchanged."""
import json,runpy,sys
from pathlib import Path
sys.path[:0]=sys.argv[1:]
from spec_generator import cli
from spec_generator.cli_io import read,bind

def gate_cli(argv,io):
    with bind(io):
        decision={'type':'founder_decision','decision_id':'srs-review','prompt':'Approve retained evidence?',
            'options':[{'value':'a','label':'Accept'}],'context':{'artifact_sha256':'a'*64}}
        answer=read(decision,'unused')
        if answer!='a':raise ValueError('wrong answer')
        out=Path(argv[argv.index('--out')+1]);out.mkdir(parents=True,exist_ok=True)
        (out/'needs.json').write_text(json.dumps({'needs':[],'accepted':True}))
cli.main=gate_cli
runpy.run_module('delivery_api.child_entry',run_name='__main__')
