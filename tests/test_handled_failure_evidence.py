import logging
from delivery_api import observability as o,diagnostic_evidence as d


def test_handled_runtime_failure_has_warning_severity_and_explicit_scope(tmp_path):
 p=tmp_path/'evidence';h=d.EvidenceHandler(p);o.logger.addHandler(h)
 try:
  o.event('engine.stream.failure','Engine stream failed',task_id='t',session_id='s',job_id='j',error='password=secret-value',console=False)
 finally:o.logger.removeHandler(h);h.close()
 text=d.read(p,task_id='t')['text']
 assert 'WARNING' in text and 'engine.stream.failure' in text and 'session_id' in text and 'job_id' in text
 assert 'secret-value' not in text


def test_expected_domain_refusal_is_not_promoted_to_exception_noise(tmp_path):
 p=tmp_path/'evidence';h=d.EvidenceHandler(p);o.logger.addHandler(h)
 try:o.event('task.export.refused','No such artifact',task_id='t',reason='not_found',console=False)
 finally:o.logger.removeHandler(h);h.close()
 assert d.read(p)['count']==0


def test_vendor_warning_reaches_durable_evidence_without_private_detail(tmp_path):
 from delivery_api import vendor_status as v
 p=tmp_path/'evidence';h=d.EvidenceHandler(p);root=logging.getLogger();root.addHandler(h);v.reset()
 try:v.warn('refresh-failed','private-provider-page',log=lambda text:None)
 finally:root.removeHandler(h);h.close();v.reset()
 text=d.read(p)['text']
 assert 'vendor.terms.warning' in text and 'refresh-failed' in text
 assert 'private-provider-page' not in text
