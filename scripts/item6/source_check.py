"""Free pre-GO source check. Reads only: no model call, nothing billed, nothing written.

Runs inside the run image WITH network, before the typed GO. It does what the admission gate will do
before the first model call: DNS, the vendor-page check (uncached) and the DeepSeek balance read.
A failure here means the run would be refused later, so it stops before any paid step."""
import datetime,socket,sys,time
from pathlib import Path


def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def chain(error):
    seen=[];out=[]
    while error is not None and id(error) not in seen:
        seen.append(id(error));out.append(type(error).__name__+': '+str(error)[:160].replace('\n',' '));error=error.__cause__ or error.__context__
    return ' <- '.join(out)


def run_checks(snapshot,balance_read,verify,resolve=socket.getaddrinfo,say=print,clock=time.monotonic):
    """Return the list of failed check names; every line carries an ISO time and the elapsed seconds."""
    failed=[];t0=clock()
    def line(text):say('%s +%.1fs  %s'%(stamp(),clock()-t0,text))
    for host in ('api.deepseek.com','api-docs.deepseek.com'):
        try:resolve(host,443);line('ok   DNS '+host)
        except Exception as error:failed.append('dns');line('FAIL DNS '+host+': '+chain(error))
    try:verify(snapshot,cached=False);line('ok   vendor pages: every pinned price, limit and the peak-hours rule match')
    except Exception as error:failed.append('vendor-pages');line('FAIL vendor pages: '+chain(error))
    try:
        read=balance_read();line('ok   DeepSeek balance read (observed '+str(read['observed_at'])+')')
    except Exception as error:failed.append('balance');line('FAIL DeepSeek balance read: '+chain(error))
    return failed


def main(config_path='/private/run.json'):
    sys.path.insert(0,'/app/item6')
    import httpx
    from mac_run import private_json
    from network_boundaries import deepseek_balance_reader,FixedOriginTransport
    from vendor_config import verify_current_sources
    config=private_json(Path(config_path))
    secrets=private_json(config['paypal_secret_file']);prices=private_json(config['catalog_file'])
    client=httpx.Client(transport=FixedOriginTransport(httpx.HTTPTransport(retries=0),'https://api.deepseek.com',lambda method,path:method=='GET' and path=='/user/balance'),trust_env=False,follow_redirects=False)
    try:
        failed=run_checks(prices[0]['provider_snapshot'],deepseek_balance_reader(client=client,api_key=secrets['deepseek_api_key']),verify_current_sources)
    finally:client.close()
    print('source check '+('FAILED: '+', '.join(failed)+'. Nothing was changed and nothing was paid.' if failed else 'passed; nothing was written.'),flush=True)
    return 1 if failed else 0


if __name__=='__main__':sys.exit(main())
