import os,pty,subprocess,sys
from pathlib import Path
SCRIPT=Path(__file__).parents[1]/'scripts/ops/terminal-color.py'
def test_severity_filter_no_color_and_plain_lines():
    text='normal\nWARNING stale\nERROR broken\nABORTED [PIN]: no price\n'
    run=subprocess.run([sys.executable,str(SCRIPT)],input=text,text=True,capture_output=True,env={**os.environ,'NO_COLOR':'1'})
    assert run.stdout==text
    env=dict(os.environ);env.pop('NO_COLOR',None)
    result=subprocess.run([sys.executable,str(SCRIPT)],input=text,text=True,capture_output=True,env=env).stdout
    assert '\033[33mWARNING' in result and '\033[31mERROR' in result
    assert result.startswith('normal\n')
def test_req_pipe_and_saved_help_are_plain():
    root=SCRIPT.parents[2]
    result=subprocess.run(['bash',str(root/'scripts/ops/req'),'help'],capture_output=True,text=True)
    assert result.returncode==0 and '\033' not in result.stdout+result.stderr
