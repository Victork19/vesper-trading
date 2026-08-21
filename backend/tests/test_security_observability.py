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
 assert c.post('/operator/rotate-key',headers={'X-Vesper-Key':'admin-test-key'}).status_code==200

def test_operational_surfaces():
 headers={'X-Vesper-Key':'client-test-key'}
 assert c.get('/observability',headers=headers).status_code==200
 assert c.get('/alerts',headers=headers).status_code==200
 assert c.get('/metrics/prometheus',headers=headers).status_code==200
