-- Explicit private proposal job shape; no execution gate or role change.
ALTER TABLE public.worker_jobs DROP CONSTRAINT worker_execution_options_shape;
ALTER TABLE public.worker_jobs ADD CONSTRAINT worker_execution_options_shape CHECK (
 jsonb_typeof(execution_options)='object' AND (
 (kind!='proposal:srs-rework' AND (
 jsonb_typeof(execution_options)='object'
 AND execution_options - 'mode' - 'model_route' = '{}'::jsonb
 AND (NOT execution_options ? 'mode' OR (jsonb_typeof(execution_options->'mode')='string' AND execution_options->>'mode' IN ('start','resume','redo')))
 AND (NOT execution_options ? 'model_route' OR
      (jsonb_typeof(execution_options->'model_route')='string' AND
       execution_options->>'model_route' ~ '^(ollama|deepseek):[^[:space:]]+$'))
)) OR (kind='proposal:srs-rework'
 AND execution_options ?& ARRAY['source_checkpoint_id','source_review_revision','item_ids','guidance']
 AND execution_options - 'source_checkpoint_id' - 'source_review_revision' - 'item_ids' - 'guidance' = '{}'::jsonb
 AND jsonb_typeof(execution_options->'source_checkpoint_id')='string'
 AND length(execution_options->>'source_checkpoint_id') BETWEEN 1 AND 200
 AND jsonb_typeof(execution_options->'source_review_revision')='string'
 AND execution_options->>'source_review_revision' ~ '^[a-f0-9]{64}$'
 AND jsonb_typeof(execution_options->'guidance')='string'
 AND length(execution_options->>'guidance') <= 7000
 AND jsonb_typeof(execution_options->'item_ids')='array'
 AND jsonb_array_length(execution_options->'item_ids') BETWEEN 1 AND 100
)));
