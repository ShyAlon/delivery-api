-- Separate settlement occurrence from later operational updates.
ALTER TABLE public.provider_attempts ADD COLUMN settled_at DOUBLE PRECISION;
-- Historical updated_at is the best available timestamp, explicitly backfilled.
UPDATE public.provider_attempts SET settled_at=updated_at WHERE state='settled';
