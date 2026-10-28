"""Fixed managed global-log budget. Per-run audit artifacts are separate."""
# size per file, file count including active. Total32MiB.
BUDGET={'diagnostics':(2*1024*1024,4),'activity':(2*1024*1024,2),
        'engine':(2*1024*1024,2),'stages':(512*1024,32)}
