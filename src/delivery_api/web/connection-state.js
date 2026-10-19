/* Background reads must never pop a dialog. Only sustained loss of the connection shows a small strip, and a success clears it. */
export function isBackground(method,opts,foregroundRead){return opts.background===true||(method==='GET'&&!foregroundRead)}

export function createConnectionState({now=()=>Date.now(),show,hide,failures=3,minMs=30000}={}){
  let count=0,since=0,shown=false;
  return {
    failed(){if(!since)since=now();count++;if(!shown&&count>=failures&&now()-since>=minMs){shown=true;show()}},
    ok(){count=0;since=0;if(shown){shown=false;hide()}},
    get shown(){return shown}
  };
}
