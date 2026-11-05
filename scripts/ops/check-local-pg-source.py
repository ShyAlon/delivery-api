#!/usr/bin/env python3
"""Read-only source shape report. Paths/check names/booleans, never values."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'docker'))
from image_settings import read_values,validate_auth,SettingsProblem
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--env-file',type=Path,default=Path.home()/'.config/requi/preview.env')
a=p.parse_args();path=a.env_file.expanduser().absolute()
report={'path':str(path),'exists':path.exists(),'regular':path.is_file(),'symlink':path.is_symlink()}
try:
    value=read_values(path)
    report.update(private_mode=True,syntax_valid=True,
      project_url_present=bool(value.get('DELIVERY_SUPABASE_URL')),
      public_key_present=bool(value.get('DELIVERY_SUPABASE_ANON_KEY')),
      operator_subject_present=bool(value.get('REQUI_OPERATOR_SUBJECT')))
    validate_auth(value);report['auth_valid']=True
except SettingsProblem as error:
    report['failed_check']=error.check
except (OSError,UnicodeError):report['failed_check']='file.readable'
print(json.dumps(report,indent=2))
