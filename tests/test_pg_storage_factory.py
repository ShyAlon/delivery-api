

def test_revision_checkpoint_modules_have_no_file_database_driver():
    import ast
    from pathlib import Path
    import delivery_api
    root=Path(delivery_api.__file__).parent
    modules=('revision_checkpoint_reader','revision_stage_writer','revision_continuation',
             'revision_stage_evidence','revision_next_review','revision_continuation_worker')
    for name in modules:
        tree=ast.parse((root/(name+'.py')).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                assert all(alias.name!='sqlite3' for alias in node.names),name
            if isinstance(node,ast.ImportFrom):
                assert node.module!='langgraph.checkpoint.sqlite',name
