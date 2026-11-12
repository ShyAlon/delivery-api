"""Version-one paste-ready instructions referring only to the approved export.

No source interpolation, model call, filesystem sweep or project defaults.
Keep this version stable: retained packages verify its exact bytes too.
"""
PROMPT_PATH='CODING_AGENT_PROMPT.md'


def render_prompt(files):
    optional=[]
    if 'spec/validation.json' in files:
        optional.append('- Read spec/validation.json for the approved validation plan.')
    if 'spec/system-baseline.json' in files:
        optional.append('- Read spec/system-baseline.json before changing the existing system; preserve its recorded constraints.')
    return '''# Coding-agent prompt

Copy the text below into your coding agent with this package available in its workspace.

---

Implement the product described by this approved specification package. Use the
included documents as the source of requirements and decisions, not guesses.

## Read before changing code

1. Read README.md for the decision digest and package reading order.
2. Read founder-decisions.json and decisions.json. Keep founder decisions,
   accepted exceptions and unresolved nonblocking inputs visible.
3. Read interview/intent_model.json and interview/conops.json for the intent,
   operating context and selected concept.
4. Read spec/needs.json, spec/srs.json and spec/architecture.json for needs,
   requirements, constraints, interfaces and the selected technical approach.
5. Follow spec/AGENTS.md and spec/manifest.json for the existing implementation
   milestones and their done-when conditions. Read each referenced item under
   spec/specs/ and the corresponding spec/items.json entry.
6. Use spec/tests.json and spec/traceability.json to map implementation and
   verification back to the approved requirements.
'''+(('\n'+'\n'.join(optional)+'\n') if optional else '')+'''
## Implement and verify

- First inspect the actual implementation workspace. Identify what already
  exists, then state which approved milestone you will implement first and why.
- Implement in the milestone order already given by spec/AGENTS.md. Keep changes
  traceable to item, requirement and acceptance-test IDs. Reuse existing code
  where it meets the spec; do not replace approved behavior unnecessarily.
- Do not invent requirements, substitute a different stack, or treat an open
  founder input as an approved answer. If the spec is conflicting or lacks a
  decision needed to proceed, name the exact files and IDs, ask the owner, and
  continue only the independent work that does not depend on that decision.
- Use only build and test commands supported by the spec or actual project
  files. If no command is defined, inspect the project and report the gap; do
  not claim an inferred command was specified or that an unrun test passed.
- Verify each milestone against its done-when conditions and the mapped
  acceptance tests. Check relevant error paths as well as the normal flow.
- Do not rewrite README.md, spec/AGENTS.md or approved requirements to make an
  implementation pass. Propose any necessary spec change separately for review.
- This prompt is not permission for deployment, purchases, production data
  changes or access to credentials. Request any separate approval needed.

## Report the result

Summarize what you implemented, the files changed, the requirement/item IDs
covered, the exact checks run and their results, and any checks not run.
List remaining blockers and the next approved milestone. Distinguish implemented
and verified work from assumptions, proposals and unverified behavior.
'''
