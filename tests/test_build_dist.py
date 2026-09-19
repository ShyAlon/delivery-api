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
    launcher=build.split("cat > \"$OUTPUT/run-require.sh\" <<'EOF'",1)[1].split('\nEOF',1)[0]
    assert 'DELIVERY_DATABASE_URL' in launcher and '$DATA_HOME/delivery.db' in launcher
    assert 'rm -rf "$OUTPUT"' in build and 'delivery.db' not in build.split('rm -rf "$OUTPUT"',1)[0]
