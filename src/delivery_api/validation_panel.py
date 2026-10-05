"""Founder-readable shape of validation.json: items grouped by the need they validate.

Pure function over the artifacts the generator already wrote. No record ids reach the
result; a need is named by its statement. Read-only."""
from __future__ import annotations


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def validation_panel(validation: dict | None, needs: dict | None, tests: list | None, ledger: dict | None) -> dict:
    """Groups: one per stakeholder need, in need order. A need with no item shows `uncovered` when the
    findings ledger holds an open validation.uncovered_need finding for it (or when it has neither items
    nor a machine test, so the panel never reads as 'fine' for a need nothing checks)."""
    items = list((validation or {}).get("items") or [])
    need_rows = list((needs or {}).get("needs") or [])
    tests = list(tests or [])
    open_uncovered = {t for f in ((ledger or {}).get("findings") or {}).values()
                      if f.get("state") == "OPEN" and f.get("rule_id") == "validation.uncovered_need"
                      for t in f.get("targets") or []}
    groups = []
    known = set()
    for need in need_rows:
        nid = need.get("need_id")
        known.add(nid)
        mine = [i for i in items if nid in (i.get("need_ids") or [])]
        groups.append({
            "need": _text(need.get("statement")) or "Stakeholder need",
            "uncovered": not mine and nid in open_uncovered,
            "items": [_item(i) for i in mine],
        })
    orphans = [i for i in items if not set(i.get("need_ids") or []) & known]
    if orphans:  # an item whose need is not in needs.json is shown, not dropped
        groups.append({"need": "Other", "uncovered": False, "items": [_item(i) for i in orphans]})
    return {"groups": groups, "item_count": len(items)}


def _item(i: dict) -> dict:
    return {
        "what_you_do": _text(i.get("what_the_human_does")),
        "what_to_look_for": _text(i.get("what_to_observe")),
        "pass_when": _text(i.get("pass_when")),
        "how_to_record_it": _text(i.get("evidence")),
        "where": _text(i.get("environment")),
        "components": [c for c in (i.get("components") or []) if _text(c)],
        "automatic_checks": len(i.get("verified_by") or []),
    }
