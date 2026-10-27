# Mock the seam, then test the real seam; and a failure nobody can read is a failure twice

2026-10-27: the startup preflight's engine check imported dotted modules with
__import__, which returns the TOP package - create_app was invisible, a bare
AssertionError refused every real start, and every image build failed its
smoke. Every hermetic test had injected a FAKE import_module, so the real
import path ran for the first time inside the image smoke. Two rules:

- When a test mocks a seam (import_module, connect), one test must exercise
  the REAL default - the seam itself is where the bugs live.
- The image smoke is part of the merge gate even without docker in the dev
  sandbox: run the smoke-equivalent checks against the source trees
  (preflight itself, ast-parse of all Python) BEFORE building an image.

And the second defect: the smoke said "(output above)" with nothing above.
full-smoke now prints a stage marker per step and, on any failure, the stage,
the exception, and the entry's log tail; the shell runners print the docker
exit code and say when the container produced no output (a signal kill,
commonly Docker Desktop running out of memory, leaves no output).
