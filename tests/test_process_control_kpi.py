from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_http_interview_path_has_no_subprocess_or_stdin():
 text=(ROOT / 'src/delivery_api/app.py').read_text();start=text.index('@app.post("/api/tasks/{task_id}/start")');end=text.index('class RequirementResolution',start);block=text[start:end]
 assert 'runner.run' not in block and 'stdin' not in block and 'interview_engine.cli' not in block

def test_production_engine_projection_has_no_prompt_comparisons():
 text=(ROOT / 'src/delivery_api/engine_projection.py').read_text()
 assert '.prompt' not in text and 're.' not in text


def test_stdout_progress_lock_wait_does_not_block_event_loop():
 import asyncio,threading
 from delivery_api.runner import ProcessRunner
 unlocked=threading.Event();entered=threading.Event();seen=[]
 class Store:
  def update_phase(self,*a,**kw):
   entered.set()
   assert unlocked.wait(0.5), 'event loop must be able to release the reader while progress waits'
   seen.append('phase')
  def add_event(self,*a,**kw):seen.append('event')
 async def check():
  runner=ProcessRunner(Store())
  work=asyncio.create_task(runner._record_legacy_progress('task','needs','ordinary progress',0,None))
  while not entered.is_set():await asyncio.sleep(.001)
  unlocked.set()
  await work
 asyncio.run(check())
 assert seen==['phase','event']
