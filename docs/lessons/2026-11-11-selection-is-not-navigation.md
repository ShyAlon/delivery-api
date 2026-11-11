# Selecting task text activated click-only surfaces

Owner report18:21/18:22: selecting a new-task title appeared to go back;
copying Home card title/description opened the task.

Reproduced current main with real dashboard/dialog/router modules and local
fixture data before editing. Four real drag tests failed before the fix.
Home card: pointerdown H2, pointerup H2 with selection, click H2 with selection,
then dashboard onclick -> navigate -> history.pushState. New-task Task name:
pointerdown INPUT, pointerup outer DIV, click outer dialog-shell, focusout;
shared backdrop removed the form. No route/history or submit fired there.

One shared gesture boundary checks native input/textarea selection as well as
DOM selection and rejects backdrop activation when the press began inside.
Use it at passive card, shared link, gate, purchase-link, header-sort and
shared backdrop boundaries. Explicit buttons and keyboard activation stay
usable. No text, field values or coordinates are logged. Bounded caller frames,
focus metadata and ignored-gesture reasons use existing client-log transport.
req debug exports the retained client navigation activity separately, with
bounds/truncation/read errors, not only the most recent Docker stdout tail.

This is a local frontend reproduction, not an owner's live-container/Mac test.
