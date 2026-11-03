#!/usr/bin/env python3
"""Zero-exception structural UI gate. Static source check, not a JS proof system."""
from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parents[2]
TAGS={'table':'table|thead|tbody|tr|th|td','dialog':'dialog','page':'header|main|footer'}
CLASSES={'table':r'(?:ui-table|audit-table)','dialog':r'(?:dialog|shared-dialog|decision-dialog|requirement-dialog)','page':r'(?:page-head|detail-head|operator-intro|operator-header|operator-bottom-bar|site-footer|compact-head)[\w-]*'}
OWNERS={kind:{'web/'+name+'.js'} for kind,name in [('table','table-component'),('dialog','shared-dialog'),('page','page-component')]}
OWNERS|={kind+'-style':files for kind,files in list(OWNERS.items())}


def without_comments(text):
    # CSS/JS block comments only. Do not strip // in strings such as href URLs.
    return re.sub(r'/\*.*?\*/','',text,flags=re.S)


def construction_count(text,kind):
    tags=TAGS[kind];classes=CLASSES[kind]
    patterns=[rf'<(?:{tags})\b',
        rf'(?<![\w$])(?:createElement|el|h)\s*\(\s*[\x27\"`](?:{tags})[\x27\"`]',
        rf'createElementNS\s*\([^,]+,\s*[\x27\"`](?:{tags})[\x27\"`]',
        rf'(?<![\w-])class\s*=\s*[\x27\"][^\x27\"]*(?<![\w-])(?:{classes})(?![\w-])',
        rf'className\s*=\s*[\x27\"`][^\x27\"`]*(?<![\w-])(?:{classes})(?![\w-])',
        rf'classList\.(?:add|toggle|replace)\s*\([^)]*[\x27\"](?:{classes})[\x27\"]']
    if kind=='dialog':patterns += [r'role\s*=\s*[\x27\"](?:dialog|alertdialog)[\x27\"]',r'setAttribute\s*\(\s*[\x27\"]role[\x27\"]\s*,\s*[\x27\"](?:dialog|alertdialog)[\x27\"]']
    return sum(len(re.findall(p,text,re.I)) for p in patterns)


def style_count(text,kind):
    # Examine actual rule preambles, including JS string/template CSS. A tag must
    # be a selector token, not a suffix like .semantic-footer or a custom property.
    text=without_comments(text).replace('\\n','\n').replace('\\"','"')
    count=0
    for pre,body in re.findall(r'([^{};]+)\{(?=([^{}]*))',text):
        if not re.match(r'\s*(?:--[\w-]+|display|position|width|height|max-width|min-width|max-height|min-height|overflow[\w-]*|padding[\w-]*|margin[\w-]*|color|background[\w-]*|border[\w-]*|font[\w-]*|transform|top|bottom|left|right|z-index|gap|grid[\w-]*|flex[\w-]*|opacity|visibility|animation|align[\w-]*|justify[\w-]*|box-shadow|cursor)\s*:',body):continue
        # JS assignment/string delimiters are not selector syntax.
        pre=re.split(r'[=\x27\"`]',pre)[-1].strip()
        if re.search(rf'\.(?:{CLASSES[kind]})(?![\w-])',pre):count+=1;continue
        if re.search(rf'(?<![\w.#-])(?:{TAGS[kind]})(?![\w-])',pre):count+=1
    return count


def inventory(root):
    result={}
    for folder in ('web','backoffice_web'):
        for path in sorted((root/folder).rglob('*')):
            if path.suffix not in ('.js','.html','.css') or 'vendor' in path.relative_to(root).parts:continue
            name=str(path.relative_to(root));text=without_comments(path.read_text())
            for kind in TAGS:
                if name not in OWNERS[kind] and path.suffix!='.css':
                    n=construction_count(text,kind)
                    if n:result[kind+':'+name]=n
                if name not in OWNERS[kind+'-style']:
                    style_text=text if path.suffix in ('.css','.js') else '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>',text,re.S)) if path.suffix=='.html' else '\n'.join(match[1] for match in re.findall(r'''([\x27\"`])((?:\\.|(?!\1).)*?)\1''',text,re.S))
                    n=style_count(style_text,kind)
                    if n:result[kind+'-style:'+name]=n
    return result


def check(root,baseline=None):
    errors=[]
    if baseline:errors.append('Nonempty legacy UI exceptions are forbidden')
    return errors+[key+' '+str(count)+' > 0' for key,count in inventory(root).items()]


if __name__=='__main__':
    baseline=json.loads((ROOT/'docs/ui-component-debt.json').read_text())
    errors=check(ROOT/'src/delivery_api',baseline)
    if errors:raise SystemExit('UI component boundary failed:\n'+'\n'.join(errors))
    print('Table/Dialog/Page construction and structural styles: zero exceptions.')
