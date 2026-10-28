# PG Start fixtures preserve reserved identity across restart

Keep Start's202,reserved session ID,visible progress and duplicate409 assertions
when moving to PG. A reconstructed adapter must recover the same durable session
without invoking Start again. Preserve exact selected model provenance instead
of catalog defaults. Fake delayed Start and health requests test responsiveness
without provider execution or external engine access.
