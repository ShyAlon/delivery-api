// Page data only. Table owns markup, behavior and styles.
export const tableSchemas={
 "active-jobs": {
  "id": "active-jobs",
  "columns": [
   "Task / title",
   "Worker job / phase",
   "Job / task state",
   "Generation",
   "Last recorded update",
   "Evidence",
   "Actions"
  ],
  "hidden": true
 },
 "sessions": {
  "id": "sessions",
  "columns": [
   {
    "label": "Task",
    "button": {
     "data-sort": "0",
     "aria-label": "Sort Task"
    }
   },
   {
    "label": "Session",
    "button": {
     "data-sort": "1",
     "aria-label": "Sort Session"
    }
   },
   {
    "label": "Generation / state",
    "button": {
     "data-sort": "2",
     "aria-label": "Sort Generation / state"
    }
   },
   "Detail"
  ],
  "hidden": true
 },
 "orphans": {
  "id": "orphans",
  "columns": [
   {
    "label": "Task",
    "button": {
     "data-sort": "0",
     "aria-label": "Sort Task"
    }
   },
   {
    "label": "Phase",
    "button": {
     "data-sort": "1",
     "aria-label": "Sort Phase"
    }
   },
   {
    "label": "Evidence",
    "button": {
     "data-sort": "2",
     "aria-label": "Sort Evidence"
    }
   },
   "Action"
  ],
  "hidden": true
 },
 "accounts": {
  "id": "accounts",
  "columns": [
   {
    "label": "Owner",
    "button": {
     "data-sort": "0",
     "aria-label": "Sort Owner"
    }
   },
   {
    "label": "Credit balance",
    "button": {
     "data-sort": "1",
     "aria-label": "Sort Credit balance"
    }
   },
   {
    "label": "Updated at (service time)",
    "button": {
     "data-sort": "2",
     "aria-label": "Sort Updated at (service time)"
    }
   },
   "Detail"
  ],
  "hidden": true
 },
 "global-sessions": {
  "id": "global-sessions",
  "columns": [
   {
    "label": "Owner",
    "button": {
     "data-sort": "0",
     "aria-label": "Sort Owner"
    }
   },
   {
    "label": "Task / session",
    "button": {
     "data-sort": "1",
     "aria-label": "Sort Task / session"
    }
   },
   {
    "label": "Generation",
    "button": {
     "data-sort": "2",
     "aria-label": "Sort Generation"
    }
   },
   {
    "label": "Created at (service time)",
    "button": {
     "data-sort": "3",
     "aria-label": "Sort Created at (service time)"
    }
   },
   "Detail"
  ],
  "hidden": true
 },
 "releases": {
  "id": "releases",
  "columns": [
   {
    "label": "When",
    "button": {
     "data-sort": "0",
     "aria-label": "Sort When"
    }
   },
   {
    "label": "Event",
    "button": {
     "data-sort": "1",
     "aria-label": "Sort Event"
    }
   },
   {
    "label": "Owner",
    "button": {
     "data-sort": "2",
     "aria-label": "Sort Owner"
    }
   },
   "Reason",
   "Attention",
   "Detail"
  ],
  "hidden": true
 },
 "acceptance-checks": {
  "id": "acceptance-checks",
  "columns": [
   "Step / check",
   "Status",
   "Reason",
   "Next action"
  ],
  "hidden": false
 },
 "backoffice-table-7": {
  "id": null,
  "columns": [
   "Provider / model",
   "Provider price USD",
   "Context / max output",
   "Fetched at",
   "Freshness",
   "Actions"
  ],
  "hidden": false
 },
 "backoffice-table-8": {
  "id": null,
  "columns": [
   {
    "label": "ID",
    "button": {
     "data-audit-sort": "id"
    }
   },
   {
    "label": "Time",
    "button": {
     "data-audit-sort": "ts"
    }
   },
   {
    "label": "Event",
    "button": {
     "data-audit-sort": "event"
    }
   },
   {
    "label": "Owner",
    "button": {
     "data-audit-sort": "owner_id"
    }
   },
   {
    "label": "Current state",
    "button": {
     "data-audit-sort": "attempt_state"
    }
   }
  ],
  "hidden": false
 }
};
