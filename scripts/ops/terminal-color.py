"""Terminal-only severity coloring. Caller enables only for a real TTY; never saved exports."""
import os,re,sys
colors={'error':'\033[31m','warning':'\033[33m'}
for line in sys.stdin:
    severity='error' if re.search(r'\b(ERROR|ABORTED|FAILED|FAILURE|FATAL|CRITICAL|[A-Z][A-Z0-9_-]*-FAIL)\b',line,re.I) else 'warning' if re.search(r'\b(WARN(?:ING)?|CAUTION)\b',line,re.I) else None
    sys.stdout.write(colors[severity]+line.rstrip('\n')+'\033[0m\n' if severity and not os.environ.get('NO_COLOR') else line)
    sys.stdout.flush()
