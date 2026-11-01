"""What a customer is allowed to read when a safety check stops work.

Customers see credits and nothing else: never vendor cost, spend limits or the names of internal checks. The raw
reason goes to the server log and the Debug client errors; the customer gets one of two plain messages."""
SAFETY='This run was paused by an account safety check. Your credits are untouched. Contact support.'
NEEDS_CREDITS='You do not have enough credits to continue. Add credits to resume.'
_CREDIT_WORDS=('balance','prepaid','insufficient','credits')
# Wording that only the admission and ledger checks produce. Narrow on purpose: ordinary run output must pass untouched.
INTERNAL_MARKERS=('AdmissionDenied','spend allowance','attempt exceeds','owner attempt','admission configuration','admission replay',
                  'expense_limit','expense limit','uncertain provider usage','unresolved compute accounting','held for review',
                  'release limit reached','vendor cost','operator expense','provider expense','provider usd','task expense ceiling')


def user_denial_message(reason):
    text=str(reason or '').lower()
    if any(word in text for word in _CREDIT_WORDS) and not any(m.lower() in text for m in ('spend allowance','expense')):return NEEDS_CREDITS
    return SAFETY


def is_internal(text):
    return any(marker.lower() in str(text).lower() for marker in INTERNAL_MARKERS)


def scrub_failure_text(text):
    """Failure text for a customer's console: any line that carries internal wording becomes the plain message, once."""
    out=[];replaced=False
    for line in str(text).split('\n'):
        if is_internal(line):
            if not replaced:out.append(user_denial_message(line));replaced=True
            continue
        out.append(line)
    return '\n'.join(out)
