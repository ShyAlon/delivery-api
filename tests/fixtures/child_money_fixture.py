"""Local fake-money child: inherited private descriptors, no provider network."""
import json
import os
import sys
from requi_child_contract.broker import BrokerClient,clients
from requi_child_contract.compute import ComputeClient,measured_work

compute=ComputeClient(int(sys.argv[2]))
broker=BrokerClient(int(sys.argv[1]),next_call_id=lambda stage:'durable-call-1')
sync,async_client=clients(broker,stage='intent',provider='deepseek',model='deepseek-flash')
assert 'DEEPSEEK_API_KEY' not in os.environ
try:
    with measured_work(compute):
        response=sync.post('https://unused.invalid/chat/completions',json={'model':'deepseek-flash','max_tokens':10})
        assert response.status_code==200 and response.json()['usage']['prompt_tokens']==2
finally:
    sync.close();broker.close();compute.close()
