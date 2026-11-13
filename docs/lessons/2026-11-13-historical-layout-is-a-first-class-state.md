# Historical review geometry is part of the layout contract

Current and historical screens used different positioning but shared one
viewport-padded action strip nested in a narrow card. A blanket control-card
margin also offset the two desktop panels. Current-only screenshots missed it.

Own decision geometry in shared Page: flex evidence/control columns, equal top
alignment and document scrolling. Delete retired grid/sticky/max-height/footer
rules at their old source. Keep original form controls and handlers; do not
build more nested drawers or copy submit controls. Historical screens show a
read-only summary rather than an instruction to continue.

Test long current AND historical evidence at desktop, phone and short/zoom
sizes. Inspect pixels, not only CSS positions. Include disabled/failed/pending
feedback, keyboard, unsaved draft preservation and exact current-review links.
The owner selected top-only actions, always visible on current decisions.
