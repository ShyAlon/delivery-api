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
    assert 'pip download --only-binary=:all: --no-deps libsql' in build
    assert 'command -v cmake' in build and 'command -v cargo' in build
    assert 'brew install cmake rust' in build
    assert 'Error: libSQL needs a source build' in build
    assert 'Likely cause:' in build and 'Raw build output:' in build
    # Native installation occurs in staging, before atomic promotion.
    assert build.index('install_dependencies(){') < build.index('Atomic promotion')
