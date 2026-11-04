"""Provider runtime construction grants no dispatch authority and has no origin mode."""
import pytest
from types import SimpleNamespace
from delivery_api.provider_runtime import construct
from delivery_api.child_pool_budget import pool_budget

@pytest.mark.parametrize('origin',['http://localhost:8090','https://requi.example'])
def test_same_reviewed_constructor_everywhere(origin):
 models={'deepseek':'reviewed'}
 runtime=construct({'DELIVERY_PUBLIC_ORIGIN':origin,'DEEPSEEK_API_KEY':'fixture-only'},SimpleNamespace(models=models))
 assert runtime.models==models and pool_budget({})==(8,4,0)

def test_key_and_reviewed_models_still_required():
 with pytest.raises(ValueError):construct({},SimpleNamespace(models={}))
 with pytest.raises(ValueError):construct({},SimpleNamespace(models={'deepseek':'reviewed'}))
 assert construct({},SimpleNamespace(models={'ollama':'reviewed'})) is None
