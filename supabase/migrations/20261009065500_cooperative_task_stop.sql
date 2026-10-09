-- Durable cooperative control, not provider cancellation. Local preparation.
ALTER TABLE public.tasks ADD COLUMN stop_requested_at DOUBLE PRECISION;
ALTER TABLE public.tasks ADD COLUMN stopped_at DOUBLE PRECISION;
ALTER TABLE public.tasks ADD COLUMN deleted_at DOUBLE PRECISION;
