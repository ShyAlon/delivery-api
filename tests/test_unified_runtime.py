"""Location never selects the business execution path; no network/provider call."""
from types import SimpleNamespace
import pytest
from delivery_api import cloud_runtime,cloud_execution,child_pool_budget

@pytest.mark.parametrize('origin',['http://localhost:8000','https://remote.example'])
def test_same_reviewed_runtime_at_every_origin(monkeypatch,tmp_path,origin):
 from delivery_api import model_pins,child_interview,child_generator
 class Gateway:
  models={'deepseek':'reviewed-model'};verify_vendor=None
  def __init__(self,*a):pass
  def refresh_launch(self):pass
  def available_environment(self):return {}
  def close(self):pass
 class Engine:
  def __init__(self,*a,**kw):self.runtime=kw['paid_runtime']
 class Host(Engine):
  def client(self):return self
  def close(self):pass
 monkeypatch.setattr(model_pins,'DurableGateways',Gateway)
 monkeypatch.setattr(child_interview,'ChildInterviewHost',Host)
 monkeypatch.setattr(child_generator,'ChildGeneratorEngine',Engine)
 cloud_runtime.uninstall();cloud_runtime._failure=None
 env={'DELIVERY_PUBLIC_ORIGIN':origin,'DELIVERY_DATABASE_URL':'postgresql://fixture/db','DEEPSEEK_API_KEY':'fixture-only','VMODEL_ARTIFACTS_DIR':str(tmp_path)}
 chosen=[]
 try:
  cloud_runtime.install(SimpleNamespace(_postgres=object()),env,chosen.append)
  assert cloud_runtime.installed() and chosen[0].runtime.models==Gateway.models
  assert child_pool_budget.pool_budget(env)==(8,4,0)
  assert cloud_execution.admitted(env)
 finally:cloud_runtime.uninstall();cloud_runtime._failure=None

def test_missing_readiness_is_fail_closed_without_location_switches():
 cloud_runtime.uninstall();cloud_runtime._failure=None
 assert not cloud_execution.admitted({})
 assert child_pool_budget.pool_budget({})==(8,4,0)


def test_runtime_has_no_deployment_mode_reads():
 import ast
 from pathlib import Path
 root=Path(__file__).parents[1]/'src/delivery_api'
 forbidden={'DELIVERY_LOCAL_PREVIEW','DELIVERY_PREVIEW','DELIVERY_CLOUD_EXECUTION','DELIVERY_CHILD_EXECUTION','DELIVERY_PAID_CHILD_PREVIEW','DELIVERY_CONTROL_PLANE_ONLY','DELIVERY_RUN_ISOLATION','DELIVERY_COOKIE_SECURE','REQUI_PIN_STORE','REQUI_RE_DIST'}
 violations=[]
 for path in root.glob('*.py'):
  for node in ast.walk(ast.parse(path.read_text())):
   if isinstance(node,ast.Constant) and isinstance(node.value,str) and node.value in forbidden:
    violations.append((path.name,node.lineno,node.value))
 assert not violations,violations
 assert not (root/'paid_preview.py').exists()
 assert not (root/'execution_fence.py').exists()

# Exhaustive names-only configuration contract: a new key requires review here.
# Values identify credentials, addresses, models, limits or build/diagnostic data.
# Artifact transition flags are explicitly isolated debt for the DB-only theme.
REVIEWED_CONFIG_KEYS=frozenset({'DELIVERY_MAX_CONCURRENT_RUNS', 'DELIVERY_METRICS_RAW_DAYS', 'DELIVERY_PORT', 'REQUI_PREFLIGHT_GENERATED_AT', 'DELIVERY_ARTIFACT_DIR', 'DELIVERY_ARTIFACT_MANIFEST_READS', 'DELIVERY_DEFAULT_USER_TOKEN_QUOTA', 'VMODEL_REVIEWED_MODEL_PRICES', 'DELIVERY_GOOGLE_CLIENT_SECRET', 'DELIVERY_MEMORY_STOP_PERCENT', 'REQUI_BUILD_REPOS', 'INTERVIEW_ENGINE_TOKEN', 'VMODEL_PYTHON', 'DELIVERY_ARTIFACT_PUBLISH', 'DELIVERY_GLOBAL_BUDGET_USD', 'DELIVERY_METRICS_INTERVAL_S', 'DELIVERY_NEXT_PHASE_MIN_CREDITS', 'DELIVERY_OLLAMA_MODEL', 'DELIVERY_ALT_ORIGINS', 'DELIVERY_GITHUB_CLIENT_ID', 'DELIVERY_RUN_START_RATE_LIMIT', 'DELIVERY_SUPABASE_ADMIN_SUBJECTS', 'DELIVERY_PUBLIC_ORIGIN', 'DELIVERY_ADMIN_EMAILS', 'INTERVIEW_ENGINE_URL', 'DELIVERY_ADMIN_TOKEN', 'DELIVERY_BASIC_AUTH_ENABLED', 'DELIVERY_LOG_SOURCE', 'REQUI_RE_RUN_STALL_TIMEOUT', 'DELIVERY_PHASE_TIME_LIMIT_S', 'DELIVERY_SESSION_SECRET', 'VMODEL_ARTIFACTS_DIR', 'DELIVERY_AUTH_RATE_LIMIT', 'INTERVIEW_ENGINE_PYTHON', 'INTERVIEW_ENGINE_QUESTION_QUALITY_CONFIG', 'DELIVERY_ARTIFACT_RESTORE', 'REQUI_PREFLIGHT_REPORT_SAVED', 'VMODEL_PROGRESS_SESSION_ID', 'DELIVERY_GOOGLE_CLIENT_ID', 'DELIVERY_HOST', 'REQUI_PIN_PROVIDERS', 'DELIVERY_SESSION_TTL_SECONDS', 'DELIVERY_DATABASE_URL', 'DELIVERY_SUPABASE_URL', 'DELIVERY_MEMORY_START_FACTOR', 'DELIVERY_MEMORY_START_FLOOR_MB', 'DELIVERY_MEMORY_WAIT_LIMIT_S', 'DELIVERY_GITHUB_CLIENT_SECRET', 'INTERVIEW_ENGINE_PORT', 'REQUI_BUILD_ID', 'DELIVERY_SUPABASE_ANON_KEY', 'DELIVERY_METRICS_BACKEND', 'DELIVERY_METRICS_SOURCE', 'DELIVERY_METRICS_IDLE_INTERVAL_S', 'REQUI_OPERATOR_SUBJECT', 'REQUI_BUILD_AT', 'INTERVIEW_ENGINE_DIR'})

def test_new_runtime_configuration_requires_value_schema_review():
 import ast
 from pathlib import Path
 unexpected=[]
 for path in (Path(__file__).parents[1]/'src/delivery_api').glob('*.py'):
  for node in ast.walk(ast.parse(path.read_text())):
   if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in ('get','getenv') and node.args and isinstance(node.args[0],ast.Constant):
    name=node.args[0].value
    if isinstance(name,str) and name.startswith(('DELIVERY_','REQUI_','INTERVIEW_','VMODEL_')) and name not in REVIEWED_CONFIG_KEYS:
     unexpected.append((path.name,node.lineno,name))
 assert not unexpected,unexpected
