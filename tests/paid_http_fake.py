"""Schema-driven fake HTTP answers; no model factory replacement."""
import itertools,json,re
_G_PLAN = [{'slug': 'api', 'kind': 'api_surface', 'title': 'API',
            'requirement_ids': ['R-1'], 'depends_on': []},
           {'slug': 'web', 'kind': 'ui_flow', 'title': 'Web',
            'requirement_ids': ['R-1'], 'depends_on': ['api']}]
_G_TEST_FILL = {'description': 'criterion holds', 'components': ['api'], 'runner': 'pytest',
                'definition': {'preconditions': [], 'steps': ['Run x'], 'expected_outcomes': ['passes'],
                               'cleanup': [], 'cases': [], 'execution': {}}}
_G_BODY_SEQ = itertools.count(1)

def _g_arch(schema, messages=None):
    """0.13.0 micro-pipeline: answer the one small call this schema shapes."""
    props = set(schema.get('properties', {}))
    if props == {'stack_keys'}:
        return {'stack_keys': ['language']}
    if props == {'technology', 'off_list_justification', 'baseline_refs'}:
        # Greenfield contract: no baseline refs may be cited.
        return {'technology': 'python', 'off_list_justification': None,
                'baseline_refs': []}
    if props == {'responsibility'}:
        return {'responsibility': 'serve'}
    if props == {'position'}:
        return {'position': 'fast'}
    if 'default_value' in props and 'options' in props:
        # Technical-choice framing for a research_required slot: the design
        # proposal the founder confirms or overrides at the gate.
        return {'title': 'Language choice', 'purpose': 'Implementation language.',
                'default_value': 'python', 'reason': 'Deterministic acceptance default.',
                'requirement_ids': (schema.get('properties', {}).get('requirement_ids', {})
                                    .get('items', {}).get('enum') or [])[:1],
                'options': [{'value': 'python', 'label': 'Python', 'implication': 'Simple.'},
                            {'value': 'node', 'label': 'Node', 'implication': 'Heavier.'}]}
    if 'driver' in props:
        refs_items = schema.get('properties', {}).get('founder_decision_refs', {}).get('items', {})
        return {'title': 't', 'decision': 'x', 'rationale': 'y', 'driver': 'simplicity',
                'options_considered': [{'option': 'z', 'reason': 'heavier'}],
                'baseline_refs': [], 'evidence': [],
                'affected_repositories': [], 'executes_concept_ids': _adr_concept_ids(messages),
                'founder_decision_refs': refs_items.get('enum') or []}
    if schema.get('type') == 'array':
        item_props = set(schema['items'].get('properties', {}))
        if 'slug' in item_props:
            return [{'slug': 't'}]
        return [{'name': 'api', 'requirement_ids': ['R-1'],
                 'baseline_refs': [], 'migration': False}]
    raise AssertionError('crosshost stub: unexpected architecture schema %r' % (schema,))

def _adr_concept_ids(messages):
    # Execution slots are pipeline-owned as execute-<concept slug>; the
    # slot JSON is quoted in the ADR body prompt. Non-execution slots
    # must declare no concept ids (exact-match stage check).
    import re as _re
    text = '\n'.join(str(getattr(m, 'content', '') or '') for m in (messages or []))
    found = _re.search(r'"slug":\s*"(execute-[a-z0-9-]+)"', text)
    return ['concept:' + found.group(1)[len('execute-'):]] if found else []

def _g_answer(stage, schema, messages=None):
    if stage == 'srs':
        if set(schema.get('properties', {})) == {'purpose', 'scope_summary'}:
            return {'purpose': 'Deterministic acceptance purpose.', 'scope_summary': 'Acceptance scope.'}
        return {'title': 'Requirement title', 'statement': 'The system shall meet the need.',
                'priority': 'must', 'acceptance': ['The criterion holds.']}
    if stage == 'items':
        if schema.get('type') == 'array':
            return _G_PLAN
        n = next(_G_BODY_SEQ)
        return {'body_markdown': '# body %d\n\nModule %d owns its own distinct contract: input shape %d, error case %d.' % (n, n, n * 7, n * 13)}
    if stage == 'tests':
        wanted = schema['properties']['fills']['required']
        return {'fills': {tid: dict(_G_TEST_FILL, description='%s holds' % tid) for tid in wanted}}
    if stage == 'architecture':
        return _g_arch(schema, messages)
    if stage == 'needs':
        return {'session_id': 's', 'needs': [{'need_id': 'N-1', 'stakeholder': 'founder',
                'statement': 'needs specs', 'kind': 'functional',
                'intent_fields': ['problem_statement'], 'intent_need_ids': ['SN-1']}]}
    if stage == 'validation':
        return {'items': []}
    if stage == 'review':
        return []
    return 'deterministic crosshost stub reply'


def answer(payload):
    messages=payload['messages'];text=json.dumps(messages)
    tools=payload.get('tools',[])
    if tools:
        tool=tools[0]['function'];schema=tool['parameters']['properties'].get('payload',{})
        stage=tool['name'].removeprefix('return_').removesuffix('_artifact')
        if stage in ('needs','srs','architecture','items','tests','validation','review'):
            return _g_answer(stage,schema,[type('Message',(),{'content':m.get('content','')})() for m in messages])
    if 'is_web_app' in text:return {'is_web_app':True,'archetype':'saas_crud','confidence':0.9,'reason':'web app'}
    if 'stakeholder_needs[]' in text:return {'stakeholder_needs':[{'need_id':'SN-1','statement':'founders need specs','kind':'functional','source_turns':[0]}]}
    if 'problem_statement, target_users[]' in text:return {'problem_statement':'p','target_users':['u'],'core_value_proposition':'v','in_scope':['a'],'out_of_scope':['b'],'source_turns':{}}
    if 'PREVIEW of 2-3' in text:return []
    if 'name and summary' in text or 'name, summary' in text:return {'name':'Native app' if 'operating concept 2 of 2' in text else 'PWA','summary':'An installable product for founders.'}
    match=re.search(r'Fill ONLY the `([^`]+)` field',text)
    if match:
        key=match.group(1)
        return {key:{'operating_environment':'phone, often offline','trigger':'habit completed','operational_flow':['open product','record points'],'exceptions':['offline'],'name':'PWA','summary':'installable web product','advantages':['one deployment'],'disadvantages':['platform limits']}[key]}
    from interview_engine.coverage_taxonomy import AREA_KEYS
    return {'question':'Anything else before we wrap up?','covered':sorted(AREA_KEYS),'thin':[],'skipped':[],'complete':True}
