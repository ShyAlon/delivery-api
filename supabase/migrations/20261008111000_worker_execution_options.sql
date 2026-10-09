-- Incremental after the fresh control-plane migration. No execution enablement.
ALTER TABLE public.worker_jobs ADD COLUMN execution_options JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.worker_jobs ADD CONSTRAINT worker_execution_options_shape CHECK (
 jsonb_typeof(execution_options)='object'
 AND execution_options - 'mode' - 'model_route' = '{}'::jsonb
 AND (NOT execution_options ? 'mode' OR (jsonb_typeof(execution_options->'mode')='string' AND execution_options->>'mode' IN ('start','resume','redo')))
 AND (NOT execution_options ? 'model_route' OR
      (jsonb_typeof(execution_options->'model_route')='string' AND
       execution_options->>'model_route' ~ '^(ollama|deepseek):[^[:space:]]+$'))
);
