"""Separate founder preferences, retained constraints and architecture reasoning.

A retained quotation is evidence to review, never founder approval of an ADR.
No matching by prose and no creation or closure of ledger findings on a read.
"""
from copy import deepcopy
from vmodel_contracts.citations import FounderDecisionCitation
from vmodel_contracts.semantic_review import digest

SUPPORT_RULE = 'architecture.founder_citation_support'


def project_decisions(architecture, preferences, preference_status, ledger=None):
    rows=[]
    findings=ledger.get('findings',{}) if isinstance(ledger,dict) else {}
    findings=list(findings.values()) if isinstance(findings,dict) else findings if isinstance(findings,list) else []
    findings=[f for f in findings if isinstance(f,dict)]
    for decision in architecture.get('decisions',[]):
        if not isinstance(decision,dict):continue
        supported=[]
        for selection in decision.get('technology_selections') or []:
            if not isinstance(selection,dict):continue
            name=str(selection.get('display_name') or '').strip().casefold()
            slot=str(selection.get('slot_id') or '').removeprefix('slot:')
            for pref in preferences:
                if pref['source_turns'] and pref['area']==slot and pref['preference'].strip().casefold()==name:
                    supported.append(deepcopy(pref))
        citations=[];invalid=False
        refs=decision.get('founder_decision_refs') or []
        for candidate in decision.get('founder_citations') or []:
            try:
                citation=FounderDecisionCitation.model_validate(candidate).model_dump(mode='json')
                if citation['source_ref'] not in refs or digest(citation['source'])!=citation['source_revision']:
                    raise ValueError('citation does not match its retained source')
                citations.append(citation)
            except (ValueError,TypeError):invalid=True
        support=None
        if citations:
            # Exact ADR/source identity only. A prior disposition for another revision cannot carry over.
            matching=[f for f in findings if f.get('rule_id')==SUPPORT_RULE
                      and decision.get('decision_id') in (f.get('targets') or [])
                      and f.get('artifact_revision')==digest({'adr':decision,'sources':citations})]
            support={'status':'support_not_yet_reviewed',
                     'question':'Does each recorded constraint support this architecture choice without extending the founder input?',
                     'finding_availability':'not_published', 'findings':[]}
            if matching:
                support['finding_availability']='available'
                support['findings']=[{k:deepcopy(f[k]) for k in ('finding_id','status','summary','disposition') if k in f} for f in matching]
                support['status']='open' if any(f.get('status') in ('OPEN','SUPERSEDED') for f in matching) else 'recorded_disposition'
        label='Founder-sourced preference' if supported else 'Recorded founder constraint; architecture choice needs review' if citations else 'Model-proposed decision'
        rows.append({'decision_id':decision.get('decision_id'),'title':decision.get('title'),
                     'label':label,'founder_evidence':supported,'baseline_refs':[str(x) for x in decision.get('baseline_refs') or []],
                     'evidence_status':preference_status,'founder_constraints':citations,
                     'constraint_status':'invalid_snapshot' if invalid else 'retained_snapshot' if citations else 'no_recorded_snapshot',
                     'model_rationale':{'driver':decision.get('driver'),'decision':decision.get('decision',''),'rationale':decision.get('rationale','')},
                     'support_review':support})
    return rows
