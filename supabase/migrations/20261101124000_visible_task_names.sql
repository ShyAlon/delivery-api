-- Retain hidden task evidence, but release its display name for a new task.
-- Transactional index replacement; owner must run reviewed migration apply.
DROP INDEX public.idx_tasks_tenant_title;
CREATE UNIQUE INDEX idx_tasks_tenant_title ON public.tasks(owner_id,lower(title)) WHERE deleted_at IS NULL;
