"""Severity belongs to the call site, not to message content."""
import os,pty,subprocess
from pathlib import Path
LOG=Path(__file__).parents[1]/'scripts/ops/terminal-log.sh'

def emit(env=None,tty=False):
    command=['bash','-c','source "$1"; echo "Plan: every failure names its step"; echo "browser error fails preview"; error "broken"; warn "stale"; exit 7','bash',str(LOG)]
    if not tty:
        return subprocess.run(command,capture_output=True,text=True,env=env)
    master,slave=pty.openpty()
    child=subprocess.Popen(command,stdout=slave,stderr=slave,env=env);os.close(slave)
    out=b''
    while True:
        try:
            chunk=os.read(master,4096)
            if not chunk:break
            out+=chunk
        except OSError:break
    os.close(master);return child.wait(),out.decode()

def test_explicit_severity_not_keyword_matching():
    env=dict(os.environ);env.pop('NO_COLOR',None)
    rc,out=emit(env,True)
    assert rc==7
    assert '\033[31mbroken\033[0m' in out and '\033[33mstale\033[0m' in out
    assert 'Plan: every failure names its step\r\n' in out
    assert 'browser error fails preview\r\n' in out
    assert '\033[31mPlan' not in out and '\033[31mbrowser' not in out

def test_pipe_file_and_no_color_stay_plain():
    result=emit()
    assert result.returncode==7 and '\033' not in result.stdout+result.stderr
    for value in ('','1'):
        rc,out=emit({**os.environ,'NO_COLOR':value},True)
        assert rc==7 and '\033' not in out

def test_req_pipe_and_saved_help_are_plain():
    result=subprocess.run(['bash',str(LOG.parent/'req'),'help'],capture_output=True,text=True)
    assert result.returncode==0 and '\033' not in result.stdout+result.stderr


def test_real_launcher_prompt_is_visible_before_answer():
    import select,time
    script=(LOG.parent/'preview-exec.sh').read_text()
    # Exercise exactly the production prompt and input statements. No launcher
    # network/DB/build is needed to prove the waiting-input boundary.
    begin=script.index("prompt 'Waiting for input:")
    end=script.index('step_ok',begin)
    body=script[begin:end]
    master,slave=pty.openpty()
    child=subprocess.Popen(['bash','-c','source "$1"; prompt_open(){ return 0; }; abort(){ exit 9; }; exec 9<&0; '+body+'echo ACCEPTED','bash',str(LOG)],stdin=slave,stdout=slave,stderr=slave,start_new_session=True,env={k:v for k,v in os.environ.items() if k!='NO_COLOR'})
    os.close(slave);out=b''
    try:
        deadline=time.monotonic()+3
        while b'anything else changes nothing:' not in out and time.monotonic()<deadline:
            if select.select([master],[],[],0.1)[0]:out+=os.read(master,4096)
        assert b'\033[36mWaiting for input:' in out,out
        assert child.poll() is None
        os.write(master,b'yes\n')
        while child.poll() is None:
            if select.select([master],[],[],0.1)[0]:
                try:out+=os.read(master,4096)
                except OSError:break
        assert child.wait(timeout=3)==0
    finally:
        if child.poll() is None:child.kill();child.wait()
        os.close(master)
