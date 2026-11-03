#!/usr/bin/env python3
"""Ratchet inherited Dialog/Page debt; Table construction has zero exceptions."""
from pathlib import Path
import json,re,sys
ROOT=Path(__file__).resolve().parents[2]
RULES={
 'table':r'<(?:table|thead|tbody|tr|th|td)\b|(?:createElement|el)\([\x27\"](?:table|thead|tbody|tr|th|td)[\x27\"]',
 'dialog':r'<(?:dialog)\b|role=[\x27\"](?:dialog|alertdialog)[\x27\"]|class=[\x27\"][^\x27\"]*\b(?:dialog|decision-dialog|requirement-dialog)\b',
 'page':r'<(?:header|main|footer)\b|class=[\x27\"][^\x27\"]*\b(?:page-head|detail-head|operator-intro)\b',
 'table-style':r'(?:\.ui-table[\w-]*|\.audit-table[\w-]*)[^{}]*\{|(?<![\w-])(?:table|thead|tbody|th|td|tr)(?![\w-])[^{}]*\{',
}
OWNERS={'table':{'web/table-component.js'},'table-style':{'web/table-component.js'},'dialog':{'web/shared-dialog.js'},'page':{'web/spa-shell.js'}}

def inventory(root):
 result={}
 for folder in ('web','backoffice_web'):
  for path in sorted((root/folder).glob('*')):
   if path.suffix not in ('.js','.html','.css'):continue
   name=str(path.relative_to(root));text=path.read_text()
   for kind,pattern in RULES.items():
    if name in OWNERS[kind]:continue
    # Style selectors are checked in stylesheets / inline style blocks only.
    body=text if path.suffix=='.css' else '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>',text,re.S)) if path.suffix=='.html' else ''
    count=len(re.findall(pattern,body if kind=='table-style' else text,re.I))
    if count:result[kind+':'+name]=count
 return result

def check(root,baseline):
 found=inventory(root)
 errors=[]
 for key,count in found.items():
  allowed=baseline.get(key,0)
  if key.startswith(('table:','table-style:')) or count>allowed:errors.append(key+' '+str(count)+' > '+str(allowed))
 for key,count in baseline.items():
  if found.get(key,0)<count:errors.append('Remove resolved debt from inventory: '+key)
 return errors

if __name__=='__main__':
 baseline=json.loads((ROOT/'docs/ui-component-debt.json').read_text())
 errors=check(ROOT/'src/delivery_api',baseline)
 if errors:raise SystemExit('UI component boundary failed:\n'+'\n'.join(errors))
 print('Table construction: zero exceptions. Dialog/Page debt: '+str(len(baseline))+' explicit entries, no expansion.')
