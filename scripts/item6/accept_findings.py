"""Accept every open review finding of a test-harness run, once, on the founder's typed phrase.

The findings stay in the ledger as REJECTED with subtype accepted_risk and this rationale; nothing is deleted or
fixed. The disposition script is the library's own founder-script format: one exact, hash-bound entry per open
finding (no wildcards). The typed phrase names the count and the hash of the exact script, so it cannot be
reused for a different ledger."""
import hashlib,json

RATIONALE='test harness run: review findings recorded and accepted, not fixed'


def build_script(ledger):
    """ledger: findings ledger dict. Returns the version 1 founder script for every OPEN finding."""
    findings=(ledger or {}).get('findings') or {}
    entries=[{'selector':{'finding_id':f['finding_id']},'action':'reject','expected_artifact_sha256':f['artifact_revision'],
              'rationale':RATIONALE,'reason_subtype':'accepted_risk'}
             for f in sorted(findings.values(),key=lambda f:f['finding_id']) if f.get('state')=='OPEN']
    return {'version':1,'authored_by':'founder','dispositions':entries}


def script_sha(script):
    return hashlib.sha256(json.dumps(script,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def phrase(script):
    return 'ACCEPT %d FINDINGS %s'%(len(script['dispositions']),script_sha(script)[:8])


def summary_lines(ledger,script):
    findings=(ledger or {}).get('findings') or {}
    open_=[f for f in findings.values() if f.get('state')=='OPEN']
    by={}
    for f in open_:by[(f.get('rule_id'),(f.get('detail') or {}).get('scope') or f.get('scope'))]=by.get((f.get('rule_id'),(f.get('detail') or {}).get('scope') or f.get('scope')),0)+1
    lines=['%d open finding(s) would be recorded as accepted risk (not fixed, kept in the ledger and the findings list):'%len(open_)]
    lines+=['  %4d  %s / %s'%(n,r,s) for (r,s),n in sorted(by.items(),key=lambda x:-x[1])]
    inc=(ledger or {}).get('incomplete') or []
    if inc:lines.append('  %d incomplete review check(s) (a chunk over its 32-finding bound) clear once these are closed'%len(inc))
    return lines


def apply_to_ledger(ledger_dict,script,current=None):
    """Apply the founder script with the library's own ledger. Returns the new ledger dict. Raises on any refusal."""
    from spec_generator.findings_ledger import Ledger
    ledger=Ledger.from_dict(ledger_dict);ledger.apply_script(script,current);return ledger.to_dict()
