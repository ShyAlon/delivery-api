-- Exact private closed-revision job options, no dispatcher/public enablement.
-- Explicit queued gate revision, retained before dispatch. No live enablement.
ALTER TABLE public.worker_jobs DROP CONSTRAINT worker_execution_options_shape;
ALTER TABLE public.worker_jobs ADD CONSTRAINT worker_execution_options_shape CHECK (COALESCE(
 jsonb_typeof(execution_options)='object' AND (
 (kind NOT IN ('proposal:srs-rework','proposal:closed-gate-revision') AND (
 jsonb_typeof(execution_options)='object'
 AND execution_options - 'mode' - 'model_route' = '{}'::jsonb
 AND (NOT execution_options ? 'mode' OR (jsonb_typeof(execution_options->'mode')='string' AND execution_options->>'mode' IN ('start','resume','redo')))
 AND (NOT execution_options ? 'model_route' OR
      (jsonb_typeof(execution_options->'model_route')='string' AND
       execution_options->>'model_route' ~ '^(ollama|deepseek):[^[:space:]]+$'))
)) OR (kind='proposal:srs-rework'
 AND execution_options ?& ARRAY['gate_revision','source_checkpoint_id','source_review_revision','item_ids','guidance']
 AND execution_options - 'gate_revision' - 'source_checkpoint_id' - 'source_review_revision' - 'item_ids' - 'guidance' = '{}'::jsonb
 AND jsonb_typeof(execution_options->'gate_revision')='number'
 AND execution_options->>'gate_revision' ~ '^[0-9]+$'
 AND jsonb_typeof(execution_options->'source_checkpoint_id')='string'
 AND length(execution_options->>'source_checkpoint_id') BETWEEN 1 AND 200
 AND jsonb_typeof(execution_options->'source_review_revision')='string'
 AND execution_options->>'source_review_revision' ~ '^[a-f0-9]{64}$'
 AND jsonb_typeof(execution_options->'guidance')='string'
 AND length(execution_options->>'guidance') <= 7000
 AND jsonb_typeof(execution_options->'item_ids')='array'
 AND jsonb_array_length(execution_options->'item_ids') BETWEEN 1 AND 100
)) OR (kind='proposal:closed-gate-revision'
 AND execution_options ?& ARRAY['phase','gate_key','gate_revision','review_revision','draft_id','preflight_digest','source_checkpoint_id','change_request','artifact_evidence','extra_cost_acknowledged']
 AND execution_options - ARRAY['phase','gate_key','gate_revision','review_revision','draft_id','preflight_digest','source_checkpoint_id','change_request','artifact_evidence','extra_cost_acknowledged'] = '{}'::jsonb
 AND jsonb_typeof(execution_options->'phase')='string' AND execution_options->>'phase' IN ('srs','architecture')
 AND execution_options->>'gate_key'='gate-'||(execution_options->>'phase')
 AND jsonb_typeof(execution_options->'gate_revision')='number' AND execution_options->>'gate_revision' ~ '^[0-9]+$'
 AND jsonb_typeof(execution_options->'review_revision')='string' AND length(execution_options->>'review_revision') BETWEEN 1 AND 200
 AND jsonb_typeof(execution_options->'draft_id')='string' AND length(execution_options->>'draft_id') BETWEEN 1 AND 200
 AND jsonb_typeof(execution_options->'source_checkpoint_id')='string' AND length(execution_options->>'source_checkpoint_id') BETWEEN 1 AND 200
 AND jsonb_typeof(execution_options->'preflight_digest')='string' AND execution_options->>'preflight_digest' ~ '^[a-f0-9]{64}$'
 AND jsonb_typeof(execution_options->'change_request')='string' AND length(btrim(execution_options->>'change_request')) BETWEEN 1 AND 20000
 AND execution_options->'extra_cost_acknowledged'='true'::jsonb
 AND jsonb_typeof(execution_options->'artifact_evidence')='array' AND jsonb_array_length(execution_options->'artifact_evidence') BETWEEN 1 AND 100
),FALSE));
