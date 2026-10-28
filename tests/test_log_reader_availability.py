from delivery_api import engine_log,diagnostic_evidence as d


def test_engine_reader_distinguishes_missing_from_unreadable(tmp_path):
 missing=engine_log.read('op',True,30,lambda s:None,path=tmp_path/'missing')
 blocked=engine_log.read('op',True,30,lambda s:None,path=tmp_path)
 assert not missing['available'] and missing.get('category') is None
 assert blocked['availability']=='unreadable' and blocked['category']=='IsADirectoryError'
 assert missing['text']==blocked['text']==''


def test_diagnostic_unreadable_is_not_empty_available_success(tmp_path):
 result=d.read(tmp_path)
 assert result['availability']=='unreadable'
 assert result['unreadable']==['IsADirectoryError']
