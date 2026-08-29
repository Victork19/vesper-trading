"""Independent, leased live-order reconciliation.

This process is deliberately independent from market ingestion. It owns no
strategy decisions; it establishes venue truth, persists progress, and refuses
to claim HALTED unless exposure is verified terminal.
"""
from __future__ import annotations
import hashlib, logging, os, signal, threading, time, uuid
from .models import Mode, OrderStatus
from .worker import (recover_submission_intents, reconcile_live_orders,
                     _atomic_apply_reconciliation, _deadline_call,
                     acquire_reconciliation_lease, finish_reconciliation_lease)

log=logging.getLogger('vesper.reconciliation')
_TERMINAL={'filled','canceled','expired','rejected','failed'}

def _venue_id(item):
    return str(item.get('id') or item.get('orderID') or item.get('venue_order_id') or item.get('order_id') or '')

def _health(memory, owner, **values):
    assignments=[];params=[]
    for key,value in values.items():
        if key=='updated_at':continue
        if value=='NOW()':
            assignments.append(f'{key}=NOW()');continue
        if key=='cycles' and value==1:
            assignments.append('cycles=cycles+1');continue
        assignments.append(f'{key}=%s');params.append(value)
    with memory.db.connection() as c:
        c.execute('INSERT INTO reconciliation_health(id,service_owner,updated_at) VALUES(1,%s,NOW()) ON CONFLICT(id) DO UPDATE SET service_owner=EXCLUDED.service_owner,updated_at=NOW()',(owner,))
        if assignments:c.execute(f"UPDATE reconciliation_health SET {','.join(assignments)},updated_at=NOW() WHERE id=1",tuple(params))

def _incident(memory, kind, venue_id, payload, status='open'):
    digest=hashlib.sha256(f'{kind}:{venue_id}'.encode()).hexdigest()[:32]
    with memory.db.connection() as c:
        c.execute("""INSERT INTO reconciliation_incidents(incident_id,kind,venue_order_id,status,payload,first_seen_at,last_seen_at)
            VALUES(%s,%s,%s,%s,%s,NOW(),NOW()) ON CONFLICT(incident_id) DO UPDATE SET status=EXCLUDED.status,payload=EXCLUDED.payload,last_seen_at=NOW(),resolved_at=CASE WHEN EXCLUDED.status='resolved' THEN NOW() ELSE NULL END""",(f'incident_{digest}',kind,venue_id,status,memory.db.json(payload)))

def _adopt_unknown(memory, item):
    """Adopt an untracked venue order into a quarantined local projection."""
    venue_id=_venue_id(item)
    if not venue_id:return None
    digest=hashlib.sha256(venue_id.encode()).hexdigest()[:32]
    size=max(0.0,float(item.get('size',item.get('original_size',item.get('quantity',0))) or 0))
    price=float(item.get('price',item.get('limit_price',0)) or 0)
    with memory.db.connection() as c:
        c.execute("""INSERT INTO orders(id,client_order_id,decision_id,mode,market_id,side,requested_size,limit_price,status,filled_size,filled_notional,filled_fees,average_fill_price,venue_order_id,error,created_at,updated_at)
            VALUES(%s,%s,%s,'live',%s,%s,%s,%s,'unknown',0,0,0,NULL,%s,%s,NOW(),NOW())
            ON CONFLICT(client_order_id) DO UPDATE SET venue_order_id=EXCLUDED.venue_order_id,error=EXCLUDED.error,updated_at=NOW()""",
            (f'unknown_venue_{digest}',f'unknown:{venue_id}',f'unknown_venue:{venue_id}',str(item.get('market_id',item.get('market','unknown'))),str(item.get('side','UNKNOWN')),size,price,venue_id,'untracked venue order adopted; reconciliation required'))
    _incident(memory,'unknown_venue_order',venue_id,item)

def _resolve_terminal_incidents(memory):
    with memory.db.connection() as c:
        c.execute("""UPDATE reconciliation_incidents i SET status='resolved',resolved_at=NOW(),last_seen_at=NOW()
            FROM orders o WHERE i.kind='unknown_venue_order' AND i.venue_order_id=o.venue_order_id
              AND o.status IN ('filled','canceled','expired','rejected','failed') AND i.resolved_at IS NULL""")

def _unresolved_incidents(memory):
    with memory.db.connection() as c:return int(c.execute("SELECT COUNT(*) AS n FROM reconciliation_incidents WHERE resolved_at IS NULL").fetchone()['n'])

def _cancel_local_orders(memory, service, owner):
    results=[]
    for order in memory.orders():
        if order.mode!=Mode.LIVE or order.status.value in _TERMINAL or not order.venue_order_id:continue
        if not acquire_reconciliation_lease(memory,order.id,owner):continue
        try:
            result=_deadline_call(service.cancel,order,timeout=os.getenv('VENUE_OPERATION_TIMEOUT_SECONDS','15'))
            if result.get('status') in {x.value for x in OrderStatus}:
                _atomic_apply_reconciliation(memory,order,result)
                finish_reconciliation_lease(memory,order.id,owner,True,status=result.get('status'))
            else:finish_reconciliation_lease(memory,order.id,owner,False,'invalid cancellation result',status='reconciliation_required')
            results.append({'order_id':order.id,**result})
        except Exception as exc:
            finish_reconciliation_lease(memory,order.id,owner,False,str(exc),status='reconciliation_required')
            results.append({'order_id':order.id,'status':'reconciliation_required','error':str(exc)})
    return results

def _cycle(memory, service, owner):
    started=time.time();_health(memory,owner,last_cycle_started_at='NOW()',cycles=1,stale=True)
    recover_submission_intents(memory,service)
    open_orders=_deadline_call(service.open_orders,timeout=os.getenv('VENUE_ENUMERATION_TIMEOUT_SECONDS',os.getenv('VENUE_OPERATION_TIMEOUT_SECONDS','15')))
    if not isinstance(open_orders,list):raise RuntimeError('venue open-order enumeration returned malformed payload')
    _health(memory,owner,last_enumeration_at='NOW()',unknown_orders=len(open_orders))
    local={order.venue_order_id for order in memory.orders() if order.mode==Mode.LIVE and order.venue_order_id}
    unknown=[item for item in open_orders if _venue_id(item) and _venue_id(item) not in local]
    if unknown:
        service.kill_switch.activate('venue has open orders absent from local ledger','system')
        for item in unknown:_adopt_unknown(memory,item)
        log.critical('adopted untracked venue orders count=%s owner=%s',len(unknown),owner)
    if service.kill_switch.active():
        _cancel_local_orders(memory,service,owner)
        for item in unknown:
            venue_id=_venue_id(item)
            try:
                final=_deadline_call(service.cancel_venue_order,venue_id,timeout=os.getenv('VENUE_OPERATION_TIMEOUT_SECONDS','15'))
                final_status=str(final.get('status','unknown')) if isinstance(final,dict) else 'unknown'
                _incident(memory,'unknown_venue_order',venue_id,{'initial':item,'cancel_result':final},status='resolved' if final_status in _TERMINAL else 'open')
            except Exception as exc:
                _incident(memory,'unknown_venue_order',venue_id,{'initial':item,'error':str(exc)})
                log.exception('unable to cancel untracked venue order=%s',venue_id)
    result=reconcile_live_orders(memory,service,owner=owner)
    _resolve_terminal_incidents(memory)
    fresh_open=_deadline_call(service.open_orders,timeout=os.getenv('VENUE_ENUMERATION_TIMEOUT_SECONDS',os.getenv('VENUE_OPERATION_TIMEOUT_SECONDS','15')))
    if not isinstance(fresh_open,list):raise RuntimeError('venue open-order re-enumeration returned malformed payload')
    outstanding=any(o.mode==Mode.LIVE and o.status.value not in _TERMINAL for o in memory.orders())
    unresolved=_unresolved_incidents(memory)
    if service.kill_switch.active() and not outstanding and not fresh_open and unresolved==0:service.kill_switch.halted()
    if service.venue:
        account=_deadline_call(service.verify_account,timeout=os.getenv('VENUE_ACCOUNT_TIMEOUT_SECONDS',os.getenv('VENUE_OPERATION_TIMEOUT_SECONDS','15')))
        _health(memory,owner,last_account_at='NOW()')
        if not account.get('verified'):service.kill_switch.activate('account reconciliation failed','system')
    _health(memory,owner,last_cycle_completed_at='NOW()',last_success_at='NOW()',last_error_at=None,last_error=None,consecutive_failures=0,orders_checked=result.get('checked',0),orders_updated=result.get('updated',0),stale=False)
    return {'result':result,'unknown':len(unknown),'outstanding':outstanding,'unresolved_incidents':unresolved,'elapsed_seconds':round(time.time()-started,3)}

def run_reconciliation_loop(memory, service, stop: threading.Event):
    interval=max(1,float(os.getenv('LIVE_ORDER_RECONCILE_SECONDS','5')));owner='reconciler_'+uuid.uuid4().hex[:12];failures=0
    while not stop.is_set():
        try:
            outcome=_cycle(memory,service,owner);failures=0;log.info('reconciliation owner=%s outcome=%s',owner,outcome)
        except Exception as exc:
            failures=min(100,failures+1)
            try:service.kill_switch.activate('reconciliation failed; venue exposure is unverified','system')
            except Exception:log.exception('unable to activate kill switch after reconciliation failure')
            try:_health(memory,owner,last_error_at='NOW()',last_error=str(exc),consecutive_failures=failures,stale=True)
            except Exception:log.exception('unable to persist reconciliation health')
            log.exception('independent reconciliation cycle failed')
        stop.wait(min(300,interval*(2**min(6,failures))))

def healthcheck():
    from .db import PostgresDatabase
    if not PostgresDatabase().reconciliation_is_healthy():raise SystemExit(1)

def main():
    if '--healthcheck' in os.sys.argv:healthcheck();return
    from .main import memory, live_execution
    stop=threading.Event()
    for name in (signal.SIGINT,signal.SIGTERM):signal.signal(name,lambda *_: stop.set())
    run_reconciliation_loop(memory,live_execution,stop)

if __name__=='__main__':main()
