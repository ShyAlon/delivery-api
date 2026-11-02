from pathlib import Path
ROOT=Path(__file__).parents[1]/'src/delivery_api'

def test_native_dialog_creation_has_one_shared_owner():
 for folder in ('web','backoffice_web'):
  for p in (ROOT/folder).glob('*.js'):
   if p.name=='shared-dialog.js':continue
   assert "createElement('dialog')" not in p.read_text(),p.name
 assert 'observeDialogs(modal,closeModal)' in (ROOT/'web/dialogs.js').read_text()


def test_all_private_dialog_actions_use_shared_status_and_fields():
 s=(ROOT/'backoffice_web/model-pins.js').read_text()
 assert 'dialogField(' in s and 'dialogActions(' in s
 assert "document.createElement('dialog')" not in s and 'function dialog(' not in s
