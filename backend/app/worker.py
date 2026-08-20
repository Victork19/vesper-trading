import os,time,logging
from .ingestion import IngestionRunner
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'));log=logging.getLogger('vesper.pipeline')
def run():
 runner=IngestionRunner();interval=int(os.getenv('PIPELINE_INTERVAL_SECONDS','60'));log.info('pipeline started interval=%ss',interval)
 while True:
  try:log.info('ingestion tick %s',runner.tick(int(os.getenv('INGEST_MARKET_LIMIT','50'))))
  except Exception as exc:runner.store.record_heartbeat(error=exc);log.exception('ingestion tick failed: %s',exc)
  time.sleep(interval)
if __name__=='__main__':run()
