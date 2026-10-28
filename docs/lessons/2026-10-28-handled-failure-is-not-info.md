# Handled failure is not informational success

Many runtime failures already had activity lines, but at INFO. A warning-only
rotating diagnostic reader would omit them. Use a reviewed stable-event set for
handled runtime failures, not substring heuristics that turn expected input/domain
refusals into exception noise. Pass explicit task/session/job fields as structured
record metadata so task-filtered diagnostics can find records without ambient
request context. Vendor warning stderr also needs a durable safe event, without
copying provider page text.
