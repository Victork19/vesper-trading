from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.security import SecurityManager

def _settings():
    return SimpleNamespace(auth_required=True, api_key='client', admin_key='admin', session_secret='x'*40, session_ttl_seconds=900)

def test_trade_identity_cannot_settle_or_administer():
    manager=SecurityManager(_settings())
    assert manager.authenticate('client','trade').scope=='trade'
    with pytest.raises(HTTPException): manager.authenticate('client','settlement_admin')
    with pytest.raises(HTTPException): manager.authenticate('client','admin')

def test_admin_is_superuser_for_operator_scopes():
    manager=SecurityManager(_settings())
    assert manager.authenticate('admin','settlement_admin').scope=='admin'
    assert manager.authenticate('admin','risk_admin').scope=='admin'

def test_risk_identity_cannot_operate_or_release():
    manager=SecurityManager(SimpleNamespace(auth_required=True, api_key='client', admin_key='admin', session_secret='x'*40, session_ttl_seconds=900))
    manager._add('risk-test','risk-secret','risk_admin')
    with pytest.raises(HTTPException) as error:
        manager.authenticate('risk-secret','operator')
    assert error.value.status_code==403

def test_privileged_session_is_bounded_and_cannot_escalate():
    settings=SimpleNamespace(auth_required=True, api_key='client', admin_key='admin', session_secret='x'*40,
                             session_ttl_seconds=28800, privileged_session_ttl_seconds=120, session_idle_seconds=120)
    manager=SecurityManager(settings)
    token,payload=manager.create_session('admin')
    assert payload['exp'] <= __import__('time').time()+121
    with pytest.raises(HTTPException) as error:
        manager.authenticate_session(token,'operator')
    assert error.value.status_code==403
