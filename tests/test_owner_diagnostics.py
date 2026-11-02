"""Reason names survive while passwords, paths, identities and SQL stay private."""
import io
from delivery_api.owner_diagnostics import failure


def test_fixed_stage_exception_and_reason_are_boolean_only():
 report={};failure(report,ValueError('configured identity differs'),'registry.read_review')
 assert report=={'failure.stage.registry.read_review':True,'failure.exception.ValueError':True,'failure.reason.configured_identity_mismatch':True}


def test_dynamic_settings_and_unknown_errors_cannot_leak_values():
 for exc in (ValueError('settings file must be private (chmod 600): /private/password'),RuntimeError('postgres://owner:SECRET@host/db'),KeyError('SECRET_DSN')):
  report={};failure(report,exc,'settings.load')
  assert all(v is True for v in report.values())
  assert not any(x in str(report) for x in ('password','SECRET','postgres://','/private/'))


def test_bounded_sqlstate_only():
 class DatabaseError(Exception):sqlstate='42501'
 report={};failure(report,DatabaseError('secret SQL'),'registry.read_review')
 assert report['failure.sqlstate.42501'] is True and 'secret SQL' not in str(report)
 DatabaseError.sqlstate='secret';report={};failure(report,DatabaseError('secret'),'registry.read_review')
 assert not any('sqlstate' in k for k in report)


def test_enrollment_dry_failure_reports_stage_without_writes(tmp_path,monkeypatch):
 from test_backoffice_enrollment import m
 monkeypatch.setattr(m.setup,'settings_module',lambda:(_ for _ in ()).throw(ValueError('settings file must be private (chmod 600): /private/secret')))
 out=io.StringIO();assert m.run(tmp_path,out=out)==1
 text=out.getvalue();assert 'failure.stage.settings.load=true' in text
 assert 'failure.exception.ValueError=true' in text and 'failure.reason.settings_permissions=true' in text
 assert 'database.updated=false' in text and 'settings.saved=false' in text and '/private/secret' not in text


def test_enrollment_missing_dsns_names_failure_and_never_connects(tmp_path,monkeypatch):
 from test_backoffice_enrollment import m
 from types import SimpleNamespace
 monkeypatch.setattr(m.setup,'settings_module',lambda:SimpleNamespace(load=lambda *a,**kw:{}))
 monkeypatch.setattr(m.setup.check,'read_connection',lambda *a:(_ for _ in ()).throw(AssertionError('must not connect')))
 out=io.StringIO();assert m.run(tmp_path,out=out)==1
 assert 'failure.reason.database_dsn_missing=true' in out.getvalue()
 assert 'REQUI_ADMIN_DATABASE_URL.present=false' in out.getvalue()


def test_config_check_emits_safe_exception_stage_on_connection_failure(tmp_path):
 from test_operator_config_check import check
 from contextlib import contextmanager
 f=tmp_path/'private.env';f.write_text('REQUI_ADMIN_DATABASE_URL=postgresql://private:secret@host/db\n');f.chmod(0o600)
 @contextmanager
 def broken(dsn):raise RuntimeError('private password secret');yield
 out=io.StringIO();assert check.run(tmp_path,explicit=f,connect=broken,out=out)==1
 text=out.getvalue();assert 'failure.stage.registry.read_check=true' in text
 assert 'failure.exception.RuntimeError=true' in text and 'password' not in text and 'secret' not in text
