import os
os.environ['SIBYL_OFFICIAL']='0'
os.environ['VESPER_AUTH_REQUIRED']='true'
os.environ['VESPER_API_KEY']='client-test-key'
os.environ['VESPER_ADMIN_KEY']='admin-test-key'
from fastapi.testclient import TestClient
from app.main import app

c=TestClient(app)

def test_scoped_authentication():
 assert c.get('/decisions').status_code==401
 assert c.get('/decisions',headers={'X-Vesper-Key':'client-test-key'}).status_code==200
 assert c.post('/operator/rotate-key',headers={'X-Vesper-Key':'client-test-key'}).status_code==403
 rotated=c.post('/operator/rotate-key',headers={'X-Vesper-Key':'admin-test-key'}).json()
 assert c.get('/decisions',headers={'X-Vesper-Key':rotated['key']}).status_code==200
 assert c.post('/operator/revoke-key/'+rotated['key_id'],headers={'X-Vesper-Key':'admin-test-key'}).status_code==200
 assert c.get('/decisions',headers={'X-Vesper-Key':rotated['key']}).status_code==401

def test_operational_surfaces():
 headers={'X-Vesper-Key':'client-test-key'}
 assert c.get('/observability',headers=headers).status_code==200
 assert c.get('/alerts',headers=headers).status_code==200
 assert c.get('/metrics/prometheus',headers=headers).status_code==200

def test_production_config_rejects_disabled_auth(monkeypatch):
 import app.config as config
 monkeypatch.setenv('VESPER_ENV','production')
 monkeypatch.setenv('DATABASE_URL','postgresql://test')
 monkeypatch.setenv('CORS_ORIGINS','https://app.example.com')
 monkeypatch.setenv('VESPER_COOKIE_SECURE','true')
 monkeypatch.setattr(config.settings,'auth_required',False)
 import pytest
 with pytest.raises(RuntimeError,match='AUTH_REQUIRED'):
  config.validate_runtime_config()

def test_production_live_config_requires_operator_secret(monkeypatch):
 import app.config as config
 monkeypatch.setenv('VESPER_ENV','production')
 monkeypatch.setenv('DATABASE_URL','postgresql://test')
 monkeypatch.setenv('CORS_ORIGINS','https://app.example.com')
 monkeypatch.setenv('VESPER_COOKIE_SECURE','true')
 monkeypatch.setattr(config.settings,'auth_required',True)
 monkeypatch.setattr(config.settings,'api_key','a'*32)
 monkeypatch.setattr(config.settings,'admin_key','b'*32)
 monkeypatch.setattr(config.settings,'session_secret','c'*32)
 monkeypatch.setattr(config.settings,'live_enabled',True)
 monkeypatch.setattr(config.settings,'max_capital',100)
 monkeypatch.setattr(config.settings,'max_order_size',10)
 monkeypatch.setattr(config.settings,'operator_approval_code','short')
 import pytest
 with pytest.raises(RuntimeError,match='OPERATOR_APPROVAL_CODE'):
  config.validate_runtime_config()
