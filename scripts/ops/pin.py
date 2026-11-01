"""Compatibility reader of durable reviewed model pins. No independent price generation."""
import os,sys
from pathlib import Path
from delivery_api.model_pins import selected_environment
from delivery_api.postgres_scope import ScopedPostgres
try:
    env=selected_environment(ScopedPostgres(os.environ['DELIVERY_DATABASE_URL']),os.environ)
    Path(sys.argv[1],'cloud-models.env').write_text('REQUI_PIN_STORE=1\nREQUI_PIN_PROVIDERS='+os.environ.get('REQUI_PIN_PROVIDERS','deepseek')+'\n')
    print('MODEL PINS loaded from shared store. Review changes in Back office > Model pins.')
except Exception as exc:raise SystemExit('PIN-FAIL '+str(exc)+'; open Back office > Model pins. Debug > Download evidence.')
