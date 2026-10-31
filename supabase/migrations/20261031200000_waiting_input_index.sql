-- Task row locking already serializes ensure-waiting-input writes. Existing
-- duplicate history remains intact; lookup chooses its earliest durable event.
CREATE INDEX events_waiting_input_identity ON public.events(task_id,(payload::jsonb->>'input_id'),id)
 WHERE kind='waiting_input';
