import os
os.environ['SIBYL_OFFICIAL']='0'
os.environ.setdefault('DATABASE_URL','postgresql://vesper:vesper@localhost:5432/vesper_test')
os.environ['VESPER_AUTH_REQUIRED']='true'
os.environ['VESPER_API_KEY']='client-test-key'
os.environ['VESPER_ADMIN_KEY']='admin-test-key'
