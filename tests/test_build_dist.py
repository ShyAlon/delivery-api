from pathlib import Path

def test_build_script_owns_single_runtime_contract():
    root=Path(__file__).parents[1]
    build=(root/'scripts/build-dist.sh').read_text()
    assert all(name in build for name in ('delivery-api','interview-engine','spec-generator','vmodel-contracts','verification-loop','vmodel-spec-app'))
    assert 'MANIFEST.txt' in build and 'run-require.sh' in build and 'REQUI_RE_DIST=1' in build
    assert 'INTERVIEW_ENGINE_PYTHON="$PYTHON"' in build

def test_launcher_health_was_exercised_by_documented_smoke_contract():
    readme=(Path(__file__).parents[1]/'README.md').read_text()
    assert 'scripts/build-dist.sh' in readme and './dist/requi-re/run-require.sh' in readme

def test_distribution_database_lives_outside_build_and_survives_rebuild():
    build=(Path(__file__).parents[1]/'scripts/build-dist.sh').read_text()
    assert 'XDG_DATA_HOME' in build and '/requi-re' in build
    launcher=build.split("cat > \"$STAGE/run-require.sh\" <<'EOF'",1)[1].split('\nEOF',1)[0]
    assert 'DELIVERY_DATABASE_URL' in launcher and '$DATA_HOME/delivery.db' in launcher
    assert 'delivery.db' not in build[:build.index("cat > \"$STAGE/run-require.sh\"")]


def test_incremental_build_has_cache_clean_and_atomic_promotion_contracts():
    build=(Path(__file__).parents[1]/'scripts/build-dist.sh').read_text()
    assert '.build-state' in build and 'decision=skip reason=unchanged' in build
    assert '--clean' in build and 'REQUI_RE_CLEAN' in build
    assert 'repo_fingerprint' in build and 'dependency-input-changed' in build
    assert '.requi-re-stage-' in build and '.requi-re-previous-' in build
    assert build.index('import delivery_api, interview_engine') < build.index('Atomic promotion')

def test_libsql_source_build_preflight_and_friendly_failure_contract():
    build=(Path(__file__).parents[1]/'scripts/build-dist.sh').read_text()
    assert 'REQUI_RE_WITH_TURSO' in build
    assert '[ "$WITH_TURSO" = 1 ] && ! "$PYTHON" -m pip download --only-binary=:all: --no-deps libsql' in build
    assert "DEPS='fastapi uvicorn pydantic httpx python-multipart langgraph langgraph-checkpoint-sqlite langchain-core langchain-openai langchain-ollama python-dotenv typesafe-sdk==0.7.0 pyarrow>=17,<24'" in build
    assert 'command -v cmake' in build and 'command -v cargo' in build
    assert 'brew install cmake rust' in build
    assert 'Error: libSQL needs a source build' in build
    assert 'Likely cause:' in build and 'Raw build output:' in build
    # Native installation occurs in staging, before atomic promotion.
    assert build.index('install_dependencies(){') < build.index('Atomic promotion')

def test_launcher_replaces_existing_owned_server_and_rejects_stale_pid_reuse():
    build=(Path(__file__).parents[1]/'scripts/build-dist.sh').read_text()
    launcher=build.split("cat > \"$STAGE/run-require.sh\" <<'EOF'",1)[1].split('\nEOF',1)[0]
    assert 'PID_FILE="$DATA_HOME/server.pid"' in launcher
    assert 'lsof -tiTCP:' in launcher and 'port ${PORT:-8000} is already used' in launcher
    assert 'Replacing running Requi.re process' in launcher and 'kill -TERM "$old_pid"' in launcher
    assert 'belongs to a different process' in launcher


def test_packaged_real_model_smoke_uses_launcher_and_http_only():
    smoke=(Path(__file__).parents[1]/'scripts/smoke-packaged-real-model.sh').read_text()
    assert 'scripts/build-dist.sh' in smoke and 'run-require.sh' in smoke
    assert '/api/tasks' in smoke and '/answer' in smoke and '/v1/sessions/not-a-session' in smoke
    assert 'subprocess' not in smoke and 'stdin' not in smoke
    assert 'test_process_control_kpi.py' in smoke


def test_launcher_streams_engine_progress_and_preserves_log():
    build=(Path(__file__).parents[1]/'scripts/build-dist.sh').read_text()
    launcher=build.split("cat > \"$STAGE/run-require.sh\" <<'EOF'",1)[1].split('\nEOF',1)[0]
    assert '> >(tee "$DATA_HOME/interview-engine.log") 2>&1 & ENGINE_PID=$!' in launcher


def test_smoke_driver_runs_from_built_runtime_not_calling_shell_python():
    smoke=(Path(__file__).parents[1]/'scripts/smoke-packaged-real-model.sh').read_text()
    assert '"$REQUI_RE_DIST_DIR/runtime/bin/python" - "$PORT"' in smoke
    assert '\npython3 - "$PORT"' not in smoke


def test_smoke_failure_reports_request_and_response():
    smoke=(Path(__file__).parents[1]/'scripts/smoke-packaged-real-model.sh').read_text()
    assert 'request={request_body!r}; response={body!r}' in smoke


def test_smoke_state_survives_rotating_diagnostic_folder():
    smoke=(Path(__file__).parents[1]/'scripts/smoke-packaged-real-model.sh').read_text()
    assert 'REQUI_RE_SMOKE_STATE_DIR' in smoke and 'checkpoint.json' in smoke
    assert 'VMODEL_ARTIFACTS_DIR="$REQUI_RE_SMOKE_STATE_DIR/artifacts"' in smoke
    assert '--fresh' in smoke and 'interview.resume' in smoke


def test_deepseek_smoke_loads_and_validates_shared_config_without_printing_key():
    smoke=(Path(__file__).parents[1]/'scripts/smoke-packaged-real-model.sh').read_text()
    assert 'SHARED_ENV="$REQUI_RE_REPOS_ROOT/.env"' in smoke
    assert '. "$SHARED_ENV"' in smoke
    assert 'DEEPSEEK_API_KEY' in smoke and 'INTERVIEW_ENGINE_MODEL_PROBING' in smoke and 'INTERVIEW_ENGINE_MODEL_DISTILLATION' in smoke
    assert 'secret not printed' in smoke
    assert 'switch-model.sh deepseek' in smoke
    assert 'case "${INTERVIEW_ENGINE_MODEL_PROBING:-}" in deepseek:*)' in smoke
    assert 'case "${INTERVIEW_ENGINE_MODEL_DISTILLATION:-}" in deepseek:*)' in smoke
    assert '[ "${INTERVIEW_ENGINE_MODEL_PROBING:-}" = deepseek:* ]' not in smoke


def test_completed_smoke_checkpoint_reuses_verified_task_before_kpi():
    smoke=(Path(__file__).parents[1]/'scripts/smoke-packaged-real-model.sh').read_text()
    assert "checkpoint['status']=='complete'" in smoke
    assert 'completed smoke checkpoint disagrees with live task' in smoke
    assert "'resumed':True" in smoke
    assert 'Reusing completed task' in smoke


def test_smoke_retries_same_request_after_server_directed_429():
    smoke=(Path(__file__).parents[1]/'scripts/smoke-packaged-real-model.sh').read_text()
    assert "if status!=429 or retry==2:break" in smoke
    assert "response_headers.get('Retry-After')" in smoke
    assert "retrying the same request" in smoke
    assert "time.sleep(wait_s)" in smoke

def test_smoke_automatically_aggregates_shadow_report_without_user_paths():
    smoke=(Path(__file__).parents[1]/'scripts/smoke-packaged-real-model.sh').read_text()
    assert 'root.rglob("categorization-shadow.jsonl")' in smoke
    assert 'REQUI_RE_REPOS_ROOT/categorization-shadow.jsonl' in smoke
    assert 'REQUI_RE_REPOS_ROOT/categorization-shadow.md' in smoke
    assert 'if files:' in smoke and 'shadow.report' in smoke
    assert 'tmp.replace(aggregate)' in smoke and 'tmp.replace(report)' in smoke


def test_pull_all_updates_exactly_six_siblings_ff_only():
    script=(Path(__file__).parents[1]/'scripts/pull-all.sh').read_text()
    assert 'delivery-api interview-engine spec-generator vmodel-contracts verification-loop vmodel-spec-app' in script
    assert 'git -C "$REPOS_ROOT/$repo" pull --ff-only' in script
    assert 'REQUI_RE_REPOS_ROOT' in script
    build=(Path(__file__).parents[1]/'scripts/build-dist.sh').read_text()
    assert build.count('$SCRIPT_DIR/pull-all.sh') >= 2

def test_launcher_terminates_and_reaps_delivery_and_engine_children():
    build=(Path(__file__).parents[1]/'scripts/build-dist.sh').read_text()
    launcher=build.split("cat > \"$STAGE/run-require.sh\" <<'EOF'",1)[1].split('\nEOF',1)[0]
    assert 'DELIVERY_PID=$!' in launcher
    assert 'kill -TERM "$DELIVERY_PID" "$ENGINE_PID"' in launcher
    assert 'wait "$DELIVERY_PID"' in launcher and 'wait "$ENGINE_PID"' in launcher
    assert 'trap terminate INT TERM' in launcher
    assert 'interview_engine.http_service' in launcher and 'Replacing orphaned interview engine' in launcher

def test_artifact_projection_prefers_typed_engine_session_binding():
    source=(Path(__file__).parents[1]/'src/delivery_api/app.py').read_text()
    block=source.split('def _recorded_engine_session',1)[1].split('\n\ndef ',1)[0]
    assert block.index('store.engine_session(task_id)') < block.index('store.events(task_id)')


def test_launcher_freezes_artifact_root_before_changing_cwd():
    build=(Path(__file__).parents[1]/'scripts/build-dist.sh').read_text()
    launcher=build.split("cat > \"$STAGE/run-require.sh\" <<'EOF'",1)[1].split('\nEOF',1)[0]
    assert 'export VMODEL_ARTIFACTS_DIR="${VMODEL_ARTIFACTS_DIR:-$PWD/artifacts}"' in launcher
    assert launcher.index('export VMODEL_ARTIFACTS_DIR=') < launcher.index('uvicorn interview_engine.http_service:app')
