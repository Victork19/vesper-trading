"""Fail-closed live execution services.

The venue client is deliberately behind a small protocol.  This keeps the
decision engine independent from the CLOB SDK and makes order lifecycle,
reconciliation, and failure behavior testable with a controlled venue.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Protocol

from .models import DecisionRecord, OrderRecord, OrderStatus, now_iso
from .config import settings
from .eda import insert_canonical_event_json, make_canonical_event


class VenueError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False, uncertain: bool = False, category: str = 'venue'):
        super().__init__(message)
        self.retryable = retryable
        self.uncertain = uncertain
        self.category = category


class VenueClient(Protocol):
    def submit_order(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def get_order(self, venue_order_id: str) -> dict[str, Any]: ...
    def get_order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None: ...
    def list_open_orders(self) -> list[dict[str, Any]]: ...
    def cancel_order(self, venue_order_id: str) -> dict[str, Any]: ...
    def health(self) -> dict[str, Any]: ...
    def account(self) -> dict[str, Any]: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    return str(value or now_iso())


def _status(value: Any) -> str:
    text = str(value or '').lower()
    mapping = {
        'open': 'accepted', 'live': 'accepted', 'accepted': 'accepted',
        'partial': 'partially_filled', 'partially_filled': 'partially_filled',
        'filled': 'filled', 'matched': 'filled', 'cancelled': 'canceled',
        'canceled': 'canceled', 'cancel_requested': 'cancel_requested',
        'rejected': 'rejected', 'expired': 'expired',
    }
    return mapping.get(text, 'unknown')


def deterministic_client_order_id(decision: DecisionRecord, attempt: int = 0) -> str:
    payload = '|'.join((decision.id, decision.market_id, decision.side or '',
                        f'{decision.executable_price if decision.executable_price is not None else decision.paper_execution_price if decision.paper_execution_price is not None else decision.price:.12f}',
                        f'{decision.size:.12f}', str(attempt)))
    return 'vesper_' + hashlib.sha256(payload.encode()).hexdigest()[:40]


@dataclass
class CircuitState:
    state: str = 'closed'
    failures: int = 0
    opened_at: str | None = None
    next_probe_at: str | None = None
    last_error: str | None = None


class ExecutionCircuit:
    def __init__(self, db, name: str = 'venue'):
        self.db = db
        self.name = name
        self.threshold = max(1, int(os.getenv('LIVE_CIRCUIT_FAILURE_THRESHOLD', '3')))
        self.cooldown = max(5, int(os.getenv('LIVE_CIRCUIT_COOLDOWN_SECONDS', '60')))
        self.probe_ttl = max(5, int(os.getenv('LIVE_CIRCUIT_PROBE_TTL_SECONDS', '15')))
        self.probe_owner = None

    def state(self) -> CircuitState:
        with self.db.connection() as c:
            row = c.execute('SELECT * FROM execution_circuit WHERE name=%s', (self.name,)).fetchone()
        if not row: return CircuitState()
        value=dict(row)
        return CircuitState(state=value.get('state','closed'), failures=int(value.get('failures',0)),
                            opened_at=_iso(value.get('opened_at')) if value.get('opened_at') else None,
                            next_probe_at=_iso(value.get('next_probe_at')) if value.get('next_probe_at') else None,
                            last_error=value.get('last_error'))

    def allow(self) -> bool:
        now = _utcnow()
        with self.db.connection() as c:
            row=c.execute('SELECT state,next_probe_at,probe_owner,probe_until FROM execution_circuit WHERE name=%s FOR UPDATE',(self.name,)).fetchone()
            if not row:
                c.execute("INSERT INTO execution_circuit(name,state,failures,updated_at) VALUES(%s,'closed',0,NOW())",(self.name,))
                return True
            if row['state']=='closed': return True
            next_probe=row['next_probe_at']
            if not next_probe or next_probe>now: return False
            if row['probe_owner'] and row['probe_until'] and row['probe_until']>now: return False
            token=uuid.uuid4().hex
            c.execute("UPDATE execution_circuit SET probe_owner=%s,probe_until=%s,updated_at=NOW() WHERE name=%s",(token,now+timedelta(seconds=self.probe_ttl),self.name))
            self.probe_owner=token
            return True

    def success(self):
        with self.db.connection() as c:
            c.execute("""INSERT INTO execution_circuit(name,state,failures,updated_at,probe_owner,probe_until)
                VALUES(%s,'closed',0,NOW()) ON CONFLICT(name) DO UPDATE SET state='closed', failures=0,
                opened_at=NULL,next_probe_at=NULL,probe_owner=NULL,probe_until=NULL,last_error=NULL,updated_at=NOW()""", (self.name,))

    def failure(self, error: str):
        with self.db.connection() as c:
            row=c.execute('SELECT failures FROM execution_circuit WHERE name=%s FOR UPDATE',(self.name,)).fetchone()
            failures=int(row['failures'])+1 if row else 1
            opened=failures>=self.threshold
            next_probe=_utcnow()+timedelta(seconds=self.cooldown) if opened else None
            c.execute("""INSERT INTO execution_circuit(name,state,failures,opened_at,next_probe_at,last_error,updated_at,probe_owner,probe_until)
                VALUES(%s,%s,%s,%s,%s,%s,NOW(),NULL,NULL) ON CONFLICT(name) DO UPDATE SET state=EXCLUDED.state,
                failures=EXCLUDED.failures,opened_at=EXCLUDED.opened_at,next_probe_at=EXCLUDED.next_probe_at,
                probe_owner=NULL,probe_until=NULL,last_error=EXCLUDED.last_error,updated_at=NOW()""", (self.name, 'open' if opened else 'closed', failures,
                _utcnow() if opened else None, next_probe, str(error)[:1000]))
        self.probe_owner=None


class LiveKillSwitch:
    def __init__(self, db): self.db = db

    def status(self) -> dict[str, Any]:
        with self.db.connection() as c:
            row = c.execute('SELECT * FROM live_kill_switch WHERE id=1').fetchone()
        return dict(row or {'active': True, 'reason': 'missing kill-switch state'})

    def active(self) -> bool: return bool(self.status().get('active', True))

    def activate(self, reason: str, actor: str):
        with self.db.connection() as c:
            c.execute("""INSERT INTO live_kill_switch(id,active,phase,reason,actor,activated_at,updated_at)
                VALUES(1,TRUE,'HALTING',%s,%s,NOW(),NOW()) ON CONFLICT(id) DO UPDATE SET active=TRUE,phase='HALTING',reason=EXCLUDED.reason,
                actor=EXCLUDED.actor,activated_at=NOW(),updated_at=NOW()""", (reason[:1000], actor))

    def halted(self):
        with self.db.connection() as c:
            c.execute("UPDATE live_kill_switch SET phase='HALTED',updated_at=NOW() WHERE id=1 AND active=TRUE")

    def release(self, actor: str):
        with self.db.connection() as c:
            row=c.execute("SELECT phase FROM live_kill_switch WHERE id=1 FOR UPDATE").fetchone()
            if row and row.get('phase') not in ('HALTED','RUNNING'): raise VenueError('kill switch cannot be released before cancellation reconciliation')
            c.execute("UPDATE live_kill_switch SET active=FALSE,phase='RUNNING',reason=NULL,released_at=NOW(),release_actor=%s,updated_at=NOW() WHERE id=1", (actor,))


class PolymarketVenue:
    """Explicit typed adapter for one pinned py-clob-client-v2 contract.

    This adapter intentionally does not probe alternative method names or
    silently downgrade to an older SDK API.  A deployment must provide the
    exact typed classes and methods below; otherwise live execution refuses to
    initialize.
    """
    def __init__(self):
        try:
            from py_clob_client_v2 import ClobClient
            from py_clob_client_v2.clob_types import ApiCreds, OrderArgs, OrderType, Side, PartialCreateOrderOptions
        except ImportError as exc:
            raise VenueError('required typed py-clob-client-v2 SDK contract is unavailable', category='configuration') from exc
        key = os.getenv('POLYMARKET_PRIVATE_KEY', '').strip()
        api_key = os.getenv('POLYMARKET_API_KEY', '').strip()
        api_secret = os.getenv('POLYMARKET_API_SECRET', '').strip()
        api_passphrase = os.getenv('POLYMARKET_API_PASSPHRASE', '').strip()
        funder = os.getenv('POLYMARKET_FUNDER_ADDRESS', '').strip()
        if not key: raise VenueError('Polymarket signer is not configured', category='configuration')
        if not all((api_key, api_secret, api_passphrase)):
            raise VenueError('Polymarket L2 API credentials are not configured', category='configuration')
        if not funder: raise VenueError('Polymarket funder is not configured', category='configuration')
        host = os.getenv('POLYMARKET_CLOB_URL', 'https://clob.polymarket.com')
        chain_id = int(os.getenv('POLYMARKET_CHAIN_ID', '137'))
        signature_type = int(os.getenv('POLYMARKET_SIGNATURE_TYPE', '0'))
        try:
            self.client = ClobClient(host, key=key, chain_id=chain_id,
                                     signature_type=signature_type, funder=funder)
            self.client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret,
                                               api_passphrase=api_passphrase))
        except Exception as exc:
            raise VenueError('Polymarket typed client initialization failed', category='configuration') from exc
        required_methods = ('create_order', 'post_order', 'get_order', 'get_orders',
                            'cancel', 'get_ok', 'get_balance_allowance')
        if any(not callable(getattr(self.client, name, None)) for name in required_methods):
            raise VenueError('Polymarket SDK lacks the required explicit execution methods', category='configuration')
        self._ApiCreds = ApiCreds
        self._OrderArgs = OrderArgs
        self._OrderType = OrderType
        self._Side = Side
        self._PartialCreateOrderOptions = PartialCreateOrderOptions
        self._signature_type = signature_type
        self._funder = funder
        self.chain_id = chain_id

    @staticmethod
    def _classify(exc: Exception, uncertain=False):
        text = str(exc).lower()
        retryable = any(token in text for token in ('timeout', 'timed out', 'temporarily', 'rate limit', '429', '502', '503', 'connection', 'network'))
        category = ('authentication' if any(token in text for token in ('unauthorized', 'forbidden', 'signature', 'credential'))
                    else 'venue_state' if any(token in text for token in ('closed', 'paused', 'expired', 'invalid price', 'invalid size', 'minimum', 'tick'))
                    else 'retryable' if retryable else 'venue')
        return VenueError('Polymarket SDK operation failed', retryable=retryable,
                          uncertain=uncertain or (retryable and any(token in text for token in ('timeout', 'timed out', 'connection', 'network'))),
                          category=category)

    @staticmethod
    def _dict(value):
        if isinstance(value, dict): return value
        for name in ('model_dump', 'dict', 'to_dict'):
            method = getattr(value, name, None)
            if callable(method):
                result = method()
                if isinstance(result, dict): return result
        raise VenueError('Polymarket SDK returned an unsupported response object', uncertain=True)

    def _invoke(self, method, *args):
        try: return method(*args)
        except VenueError: raise
        except Exception as exc: raise self._classify(exc) from exc

    def submit_order(self, request):
        token_id = str(request.get('token_id') or '').strip()
        if not token_id or request.get('side') != 'BUY':
            raise VenueError('live order must contain a token identity and venue side BUY', category='configuration')
        try:
            args = self._OrderArgs(price=float(request['price']), size=float(request['size']),
                                   side=self._Side.BUY, token_id=token_id,
                                   client_order_id=str(request['client_order_id']))
            options = self._PartialCreateOrderOptions(
                tick_size=str(request['tick_size']), neg_risk=bool(request['neg_risk']))
        except Exception as exc:
            raise VenueError('typed SDK order arguments or client-order identity are unsupported', category='configuration') from exc
        signed = self._invoke(self.client.create_order, args, options)
        response = self._dict(self._invoke(self.client.post_order, signed, self._OrderType.GTC))
        venue_id = response.get('orderID') or response.get('orderId') or response.get('id')
        if not venue_id: raise VenueError('CLOB response did not include an order ID', uncertain=True)
        return self._normalize(response, str(venue_id), request)

    def get_order(self, venue_order_id):
        return self._dict(self._invoke(self.client.get_order, venue_order_id))

    def get_order_by_client_id(self, client_order_id):
        # The explicit SDK contract has no assumed client-id lookup method.
        # We use the typed order listing only when it returns a client ID.
        rows = self._orders()
        for row in rows:
            if row.get('client_order_id') == client_order_id or row.get('clientOrderId') == client_order_id:
                return row
        return None

    def list_open_orders(self):
        terminal={'filled','canceled','expired','rejected','failed'}
        return [row for row in self._orders() if _status(row.get('status',row.get('order_status'))) not in terminal]

    def _orders(self):
        response = self._dict(self._invoke(self.client.get_orders))
        rows = response.get('orders', response.get('data', response))
        if not isinstance(rows, list):
            raise VenueError('Polymarket order-list response is not a list', uncertain=True)
        return [self._dict(row) for row in rows]

    def cancel_order(self, venue_order_id):
        return self._dict(self._invoke(self.client.cancel, venue_order_id))

    def health(self):
        try:
            response = self._dict(self._invoke(self.client.get_ok))
            return {'healthy': True, 'response': response}
        except VenueError: return {'healthy': False, 'error': 'venue_health_check_failed'}

    def account(self):
        return self._dict(self._invoke(self.client.get_balance_allowance))

    @staticmethod
    def _normalize(response, venue_id, request):
        status = response.get('status', response.get('order_status', 'accepted'))
        return {'venue_order_id': venue_id, 'client_order_id': request['client_order_id'],
                'status': status, 'filled_size': float(response.get('filled_size', response.get('size_matched', 0)) or 0),
                'average_fill_price': response.get('average_fill_price', response.get('avg_price')),
                'fills': response.get('fills', []), **response}


class ControlledVenue:
    """Deterministic venue for controlled-account and failure-drill tests.

    This class never talks to a network.  It models acceptance, partial fills,
    cancellation, outages, and account verification behind the same protocol
    used by the real CLOB adapter.
    """
    def __init__(self, balance: float = 1000.0, allowance: float = 1000.0, partial_fill: float = 1.0):
        self.balance=balance; self.allowance=allowance; self.partial_fill=partial_fill; self.fail=False; self.orders={}; self.counter=0
    def health(self):
        if self.fail: return {'healthy': False, 'error': 'controlled outage'}
        return {'healthy': True}
    def account(self):
        if self.fail: raise VenueError('controlled outage', retryable=True)
        return {'collateral_balance': self.balance, 'allowances': {'default': self.allowance}}
    def submit_order(self, request):
        if self.fail: raise VenueError('controlled outage', retryable=True, uncertain=True)
        self.counter += 1; venue_id=f'controlled-{self.counter}'; filled=float(request['size'])*max(0,min(1,self.partial_fill))
        status='filled' if filled >= float(request['size']) else 'partially_filled'
        self.orders[venue_id]={'status':status,'filled_size':filled,'average_fill_price':float(request['price']),**request};return {'venue_order_id':venue_id,**self.orders[venue_id]}
    def get_order(self, venue_order_id):
        if self.fail: raise VenueError('controlled outage', retryable=True)
        return self.orders.get(venue_order_id, {'status':'rejected'})
    def get_order_by_client_id(self, client_order_id):
        return next((value for value in self.orders.values() if value.get('client_order_id')==client_order_id),None)
    def list_open_orders(self):
        return [value for value in self.orders.values() if value.get('status') not in ('filled','canceled','rejected','expired')]
    def cancel_order(self, venue_order_id):
        if self.fail: raise VenueError('controlled outage', retryable=True)
        order=self.orders.get(venue_order_id)
        if not order: return {'status':'rejected'}
        if order['status'] != 'filled': order['status']='canceled'
        return order


class LiveExecutionService:
    def __init__(self, db, venue: VenueClient | None = None):
        self.db = db
        self.venue = venue
        self.submission_circuit = ExecutionCircuit(db,'submission')
        self.reconciliation_circuit = ExecutionCircuit(db,'reconciliation')
        self.cancellation_circuit = ExecutionCircuit(db,'cancellation')
        self.account_circuit = ExecutionCircuit(db,'account')
        self.kill_switch = LiveKillSwitch(db)
        self.max_retries = max(0, int(os.getenv('LIVE_MAX_RETRIES', '2')))

    def set_venue(self, venue): self.venue = venue

    def _eda_event(self, decision_id: str | None, event_type: str, payload: dict[str, Any], source_event_id: str, connection=None):
        if not decision_id:
            return
        def write(c):
            row = c.execute('SELECT episode_id FROM eda_episodes WHERE decision_id=%s', (decision_id,)).fetchone()
            if not row:
                raise VenueError(f'EDA episode missing for decision {decision_id}', category='provenance')
            event = make_canonical_event('execution', event_type, payload, episode_id=row['episode_id'], source_event_id=source_event_id)
            event.event_id = 'eda_execution_' + hashlib.sha256(source_event_id.encode()).hexdigest()
            insert_canonical_event_json(c, self.db, event)
        if connection is not None: write(connection)
        else:
            with self.db.connection() as c: write(c)

    def _ledger(self, event_type, idempotency_key, order_id=None, decision_id=None,
                quantity=0, notional=0, fee=0, payload=None):
        """Append one immutable financial execution event."""
        with self.db.connection() as c:
            c.execute("""INSERT INTO execution_ledger
                (event_id,idempotency_key,order_id,decision_id,event_type,quantity,notional,fee,payload,observed_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) ON CONFLICT(idempotency_key) DO NOTHING""",
                ('ledger_'+uuid.uuid4().hex, idempotency_key, order_id, decision_id,
                 event_type, max(0,float(quantity)), max(0,float(notional)), max(0,float(fee)), self.db.json(payload or {})))
            self._eda_event(decision_id, event_type, payload or {}, idempotency_key, connection=c)

    def recover_submission_intent(self, client_order_id: str) -> dict[str, Any] | None:
        """Resolve a pending/uncertain intent without submitting again."""
        if not self.venue:return None
        try:
            response=self.venue.get_order_by_client_id(client_order_id)
            if not response:return None
            status=_status(response.get('status'))
            if status=='unknown':return None
            return {'status':status,'client_order_id':client_order_id,'venue_order_id':response.get('venue_order_id') or response.get('orderID') or response.get('id'),'filled_size':float(response.get('filled_size',response.get('size_matched',0)) or 0),'average_fill_price':response.get('average_fill_price',response.get('avg_price')),'response':response}
        except Exception as exc:
            return {'status':'reconciliation_required','error':str(exc)}

    def _ensure_venue(self):
        if self.venue is None:
            self.venue = PolymarketVenue()
        return self.venue

    def readiness(self) -> dict[str, Any]:
        if self.venue is None and os.getenv('POLYMARKET_PRIVATE_KEY', '').strip():
            try: self._ensure_venue()
            except VenueError: pass
        checks = {'configured': bool(os.getenv('POLYMARKET_PRIVATE_KEY')),
                  'signer_configured': bool(os.getenv('POLYMARKET_SIGNER_ADDRESS')),
                  'funder_configured': bool(os.getenv('POLYMARKET_FUNDER_ADDRESS')),
                  'allowance_configured': bool(os.getenv('POLYMARKET_REQUIRED_ALLOWANCE')),
                  'exchange_configured': bool(os.getenv('POLYMARKET_EXCHANGE_CONTRACT')),
                  'chain_verified': os.getenv('POLYMARKET_CHAIN_ID','137')=='137',
                  'venue_client': self.venue is not None, 'kill_switch_clear': not self.kill_switch.active(),
                  'circuit_closed': all(circuit.allow() for circuit in (self.submission_circuit,self.reconciliation_circuit,self.cancellation_circuit,self.account_circuit))}
        if self.venue:
            try:
                health = self.venue.health(); checks['venue_healthy'] = bool(health.get('healthy'))
            except Exception as exc: checks['venue_healthy'] = False; checks['venue_error'] = str(exc)
        else: checks['venue_healthy'] = False
        if self.venue and checks.get('venue_healthy'):
            checks['account_verified']=bool(self.verify_account().get('verified'))
        else: checks['account_verified']=False
        return {'ready': all(checks.values()), 'checks': checks, 'kill_switch': self.kill_switch.status(), 'circuits': {name:circuit.state().__dict__ for name,circuit in {'submission':self.submission_circuit,'reconciliation':self.reconciliation_circuit,'cancellation':self.cancellation_circuit,'account':self.account_circuit}.items()}}

    def verify_account(self) -> dict[str, Any]:
        if not self.account_circuit.allow():
            return {'verified': False, 'reason': 'account circuit breaker is open'}
        configured_signer=os.getenv('POLYMARKET_SIGNER_ADDRESS','').strip().lower()
        configured_funder=os.getenv('POLYMARKET_FUNDER_ADDRESS','').strip().lower()
        if not configured_signer or not configured_funder:
            return {'verified': False, 'reason': 'signer and funder addresses are required', 'signer_configured': bool(configured_signer), 'funder_configured': bool(configured_funder)}
        if not self.venue: return {'verified': False, 'reason': 'venue client unavailable'}
        try:
            account = self.venue.account()
            if not isinstance(account,dict): raise VenueError('venue account response must be an object',category='account')
            required = os.getenv('POLYMARKET_REQUIRED_ALLOWANCE','').strip()
            exchange = os.getenv('POLYMARKET_EXCHANGE_CONTRACT','').strip().lower()
            allowances = account.get('allowances')
            if not isinstance(allowances,dict) or not required: raise VenueError('explicit collateral allowance configuration is required',category='account')
            allowance_value=allowances.get(required)
            allowance_ok=allowance_value is not None and float(allowance_value)>0
            balance = float(account.get('collateral_balance', account.get('balance', 0)) or 0)
            minimum = float(os.getenv('MIN_LIVE_COLLATERAL_BALANCE', '0'))
            venue_signer=str(account.get('signer_address',account.get('signer','')) or '').lower()
            venue_funder=str(account.get('funder_address',account.get('funder','')) or '').lower()
            venue_chain=str(account.get('chain_id',account.get('chainId','')) or '')
            venue_exchange=str(account.get('exchange_contract',account.get('exchange','')) or '').lower()
            signer_ok=bool(venue_signer) and venue_signer==configured_signer
            funder_ok=bool(venue_funder) and venue_funder==configured_funder
            chain_ok=venue_chain==os.getenv('POLYMARKET_CHAIN_ID','137')
            exchange_ok=bool(exchange) and venue_exchange==exchange
            with self.db.connection() as c:
                reserved=float(c.execute("SELECT COALESCE(SUM(requested_capital+fee_reserve),0) AS value FROM capital_reservations WHERE state IN ('reserved','submitted','uncertain','partially_filled')").fetchone()['value'] or 0)
            available=balance-reserved;capital_ok=available>=-1e-12
            result = {'verified': allowance_ok and balance >= minimum and signer_ok and funder_ok and chain_ok and exchange_ok and capital_ok, 'balance': balance, 'reserved_capital': reserved, 'available_capital': available, 'allowances': allowances, 'allowance_ok': allowance_ok, 'signer_ok': signer_ok, 'funder_ok': funder_ok, 'chain_ok': chain_ok, 'exchange_ok': exchange_ok, 'capital_ok': capital_ok, 'payload': account}
            self._save_account(result)
            self.account_circuit.success()
            return result
        except Exception as exc:
            self.account_circuit.failure(str(exc))
            return {'verified': False, 'reason': str(exc)}

    def _save_account(self, account):
        with self.db.connection() as c:
            reserved=c.execute("SELECT COALESCE(SUM(requested_capital),0) AS value FROM capital_reservations WHERE state IN ('reserved','submitted','uncertain','partially_filled')").fetchone()['value']
            balance=float(account.get('balance',0) or 0);available=balance-float(reserved or 0)
            c.execute('INSERT INTO account_snapshots(source,collateral_balance,reserved_capital,available_capital,allowances,payload,observed_at) VALUES(%s,%s,%s,%s,%s,%s,NOW())',('venue',balance,reserved,available,self.db.json(account.get('allowances',{})),self.db.json(account)))
            c.execute('INSERT INTO risk_snapshots(available_capital,reserved_capital,venue_collateral,mismatch,blocked,reason,observed_at) VALUES(%s,%s,%s,%s,%s,%s,NOW())',(available,reserved,balance,max(0,-available),available<0,'reserved capital exceeds venue collateral' if available<0 else None))

    def reserve_capital(self, decision: DecisionRecord, client_id: str, price: float):
        requested=max(0,float(decision.size)*float(price));fee=requested*max(0,float(os.getenv('LIVE_FEE_RESERVE_RATE','0')));limit=float(os.getenv('MAX_LIVE_CAPITAL','0'))
        if settings.deployment_stage=='canary':limit=min(limit,settings.canary_max_capital)
        if limit<=0: raise VenueError('live capital limit is not configured',category='risk')
        with self.db.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtext('vesper-live-capital'))")
            used=c.execute("SELECT COALESCE(SUM(requested_capital+fee_reserve),0) AS value FROM capital_reservations WHERE state IN ('reserved','submitted','uncertain','partially_filled')").fetchone()['value']
            if float(used or 0)+requested+fee>limit+1e-12: raise VenueError('aggregate capital limit would be exceeded',category='risk')
            c.execute("INSERT INTO capital_reservations(decision_id,client_order_id,requested_capital,fee_reserve,state) VALUES(%s,%s,%s,%s,'reserved') ON CONFLICT(client_order_id) DO NOTHING",(decision.id,client_id,requested,fee))

    def update_reservation(self, client_id: str, state: str, filled_capital: float = 0, order_id: str | None = None):
        allowed={'reserved','submitted','accepted','uncertain','partially_filled','filled','cancel_requested','canceled','expired','rejected','failed','released','settled'}
        if state not in allowed: raise ValueError('invalid reservation state')
        with self.db.connection() as c:
            c.execute("UPDATE capital_reservations SET state=%s,filled_capital=%s,order_id=COALESCE(%s,order_id),released_at=CASE WHEN %s IN ('filled','canceled','expired','rejected','failed','released','settled') THEN NOW() ELSE released_at END WHERE client_order_id=%s",(state,max(0,float(filled_capital)),order_id,state,client_id))

    def _record_attempt(self, decision, client_id, attempt, action, status, request, response=None, error=None):
        with self.db.connection() as c:
            c.execute('INSERT INTO execution_attempts(decision_id,client_order_id,attempt,action,status,request,response,error,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NOW()) ON CONFLICT(decision_id,attempt,action) DO UPDATE SET status=EXCLUDED.status,response=EXCLUDED.response,error=EXCLUDED.error',
                      (decision.id, client_id, attempt, action, status, self.db.json(request), self.db.json(response) if response is not None else self.db.json({}), error))
            self._eda_event(decision.id, 'execution_attempt', {'client_order_id': client_id, 'attempt': attempt, 'action': action, 'status': status, 'request': request, 'response': response or {}, 'error': error}, f'{client_id}:{action}:{attempt}:{status}', connection=c)

    def _record_order_attempt(self, order, action, status, request=None, response=None, error=None):
        attempt = int(time.time_ns() // 1000)
        with self.db.connection() as c:
            c.execute('''INSERT INTO execution_attempts(decision_id,client_order_id,attempt,action,status,request,response,error,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NOW())''',
                (order.decision_id,order.client_order_id,attempt,action,status,self.db.json(request or {'venue_order_id':order.venue_order_id}),self.db.json(response or {}),error))

    def _claim_submission(self, decision, client_id, request):
        """Claim the external side effect before calling the venue.

        A pending claim is intentionally treated as uncertain after a crash;
        submitting again is less safe than requiring reconciliation.
        """
        with self.db.connection() as c:
            inserted=c.execute("""INSERT INTO execution_attempts(decision_id,client_order_id,attempt,action,status,request,response,created_at)
                VALUES(%s,%s,-1,'intent','pending',%s,%s,NOW()) ON CONFLICT(decision_id,attempt,action) DO NOTHING RETURNING id""",
                (decision.id,client_id,self.db.json(request),self.db.json({}))).fetchone()
            if inserted:return {'claimed':True}
            row=c.execute("SELECT status,response,error FROM execution_attempts WHERE decision_id=%s AND client_order_id=%s AND attempt=-1 AND action='intent' FOR UPDATE",(decision.id,client_id)).fetchone()
        if not row: raise VenueError('submission intent disappeared; reconciliation required',uncertain=True)
        if row['status'] in ('accepted','filled','partially_filled'):
            response=row['response'] or {}
            return {'claimed':False,'result':{'status':_status(response.get('status','accepted')),'client_order_id':client_id,'venue_order_id':response.get('venue_order_id') or response.get('id'),'filled_size':float(response.get('filled_size',0) or 0),'average_fill_price':response.get('average_fill_price')}}
        raise VenueError('existing submission intent requires reconciliation before retry',uncertain=True)

    def _complete_intent(self, decision, client_id, status, response=None, error=None):
        with self.db.connection() as c:
            c.execute("UPDATE execution_attempts SET status=%s,response=%s,error=%s WHERE decision_id=%s AND client_order_id=%s AND attempt=-1 AND action='intent'",
                      (status,self.db.json(response or {}),error,decision.id,client_id))

    def submit(self, decision: DecisionRecord) -> dict[str, Any]:
        if self.kill_switch.active(): raise VenueError('live kill switch is active')
        if not self.submission_circuit.allow(): raise VenueError('submission circuit breaker is open')
        try: self._ensure_venue()
        except VenueError as exc: raise
        account = self.verify_account()
        if not account.get('verified'): raise VenueError('wallet, balance, or allowance verification failed')
        client_id = deterministic_client_order_id(decision)
        with self.db.connection() as c:
            existing = c.execute("SELECT * FROM orders WHERE client_order_id=%s AND status NOT IN ('failed','rejected','canceled')", (client_id,)).fetchone()
        if existing: return dict(existing)
        token_id = decision.market_context.get('yes_token_id' if decision.side == 'YES' else 'no_token_id')
        price = decision.executable_price if decision.executable_price is not None else decision.paper_execution_price if decision.paper_execution_price is not None else decision.price
        tick_size = decision.market_context.get('tick_size')
        neg_risk = decision.market_context.get('neg_risk')
        if not token_id or not tick_size or neg_risk is None:
            raise VenueError('live market metadata lacks token, tick size, or neg-risk configuration', category='venue_state')
        request = {'client_order_id': client_id, 'market_id': decision.market_id, 'outcome': decision.side,
                   'side': 'BUY', 'size': decision.size, 'price': price, 'tick_size': tick_size,
                   'neg_risk': bool(neg_risk), 'decision_id': decision.id, 'token_id': str(token_id)}
        self.reserve_capital(decision,client_id,float(request['price']))
        claim=self._claim_submission(decision,client_id,request)
        if not claim.get('claimed'):return claim['result']
        last = None
        for attempt in range(self.max_retries + 1):
            self._record_attempt(decision, client_id, attempt, 'submit', 'started', request)
            try:
                response = self.venue.submit_order(request)
                venue_id = str(response.get('venue_order_id') or response.get('id'))
                status = _status(response.get('status', 'accepted'))
                reported_filled=float(response.get('filled_size',0) or 0)
                # Aggregate-only fills are not authoritative. Keep the order
                # reconcilable, but never allow aggregate venue fields to
                # become the financial source of truth.
                if reported_filled>0 and not isinstance(response.get('fills'),list):
                    status='reconciliation_required'
                self._record_attempt(decision, client_id, attempt, 'submit', status, request, response)
                self._complete_intent(decision,client_id,status,response=response)
                self._ledger('submission_ack',f'{client_id}:submit:{attempt}',decision_id=decision.id,payload=response)
                reservation_state='partially_filled' if status=='partially_filled' else 'uncertain' if status=='reconciliation_required' else 'submitted' if status in ('accepted','unknown') else status
                self.update_reservation(client_id,reservation_state,0 if status=='reconciliation_required' else reported_filled*float(response.get('average_fill_price',request['price']) or request['price']))
                self.submission_circuit.success()
                return {'status': status, 'client_order_id': client_id, 'venue_order_id': venue_id,
                        'filled_size': float(response.get('filled_size', 0) or 0), 'average_fill_price': response.get('average_fill_price'),
                        'fills': response.get('fills', [])}
            except VenueError as exc:
                last = exc; self._record_attempt(decision, client_id, attempt, 'submit', 'failed', request, error=str(exc))
                if exc.uncertain:
                    self._complete_intent(decision,client_id,'uncertain',error=str(exc))
                    self.update_reservation(client_id,'uncertain')
                    raise VenueError('order submission is uncertain; reconciliation required', uncertain=True) from exc
                if not exc.retryable or attempt >= self.max_retries: break
                time.sleep(min(10, .25 * (2 ** attempt)))
        self._complete_intent(decision,client_id,'failed',error=str(last) if last else 'order submission failed')
        self.update_reservation(client_id,'failed')
        self.submission_circuit.failure(str(last))
        if self.submission_circuit.state().state == 'open':
            self.kill_switch.activate('venue circuit breaker opened after repeated submission failures', 'system')
        raise last or VenueError('order submission failed')

    def _ingest_fills(self, order: OrderRecord, response: dict[str, Any]) -> None:
        fills = response.get('fills')
        if not isinstance(fills, list):
            return
        with self.db.connection() as c:
            for fill in fills:
                if not isinstance(fill, dict):
                    raise VenueError('venue returned a malformed fill', uncertain=True)
                venue_fill_id = fill.get('id') or fill.get('fill_id') or fill.get('trade_id')
                if not venue_fill_id:
                    raise VenueError('live fill has no stable venue fill identity', uncertain=True)
                fill_id = str(venue_fill_id)
                quantity = float(fill.get('size', fill.get('quantity', 0)) or 0)
                fill_price = float(fill.get('price', response.get('average_fill_price', response.get('avg_price', 0))) or 0)
                fill_fee = float(fill.get('fee', 0) or 0)
                if quantity <= 0 or not 0 <= fill_price <= 1 or fill_fee < 0:
                    raise VenueError('venue returned an invalid fill', uncertain=True)
                c.execute('''INSERT INTO execution_fills(fill_id,order_id,venue_order_id,quantity,price,fee,payload,observed_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,NOW()) ON CONFLICT(fill_id) DO NOTHING''',
                    (fill_id, order.id, order.venue_order_id, quantity, fill_price, fill_fee, self.db.json(fill)))
                c.execute('''INSERT INTO execution_ledger
                    (event_id,idempotency_key,order_id,decision_id,event_type,quantity,notional,fee,payload,observed_at)
                    VALUES(%s,%s,%s,%s,'fill',%s,%s,%s,%s,NOW()) ON CONFLICT(idempotency_key) DO NOTHING''',
                    ('ledger_fill_'+fill_id, 'fill:'+fill_id, order.id, order.decision_id,
                     quantity, quantity*fill_price, fill_fee, self.db.json(fill)))
                self._eda_event(order.decision_id, 'fill_observed', {'order_id': order.id, 'fill_id': fill_id, 'quantity': quantity, 'price': fill_price, 'fee': fill_fee, 'payload': fill}, f'fill:{fill_id}', connection=c)

    def _fill_totals(self, order: OrderRecord):
        with self.db.connection() as c:
            return c.execute('''SELECT COALESCE(SUM(quantity),0) AS quantity,
                COALESCE(SUM(quantity*price),0) AS notional,
                COALESCE(SUM(fee),0) AS fees FROM execution_fills WHERE order_id=%s''', (order.id,)).fetchone()

    def reconcile(self, order: OrderRecord) -> dict[str, Any]:
        if not self.venue or not order.venue_order_id: return {'status': 'reconciliation_required', 'reason': 'missing venue order'}
        try:
            response = self.venue.get_order(order.venue_order_id)
            status = _status(response.get('status'))
            reported_filled = float(response.get('filled_size', response.get('size_matched', order.filled_size)) or 0)
            reported_price = response.get('average_fill_price', response.get('avg_price', order.average_fill_price))
            self._ingest_fills(order, response)
            totals=self._fill_totals(order)
            filled=float(totals['quantity'] or 0);notional=float(totals['notional'] or 0)
            fees=float(totals['fees'] or 0)
            if reported_filled>0 and filled<=0:
                raise VenueError('venue returned aggregate fills without immutable fill records',uncertain=True)
            if reported_filled>0 and abs(reported_filled-filled)>1e-9:
                raise VenueError('venue aggregate fill total does not match immutable fill records',uncertain=True)
            if filled+1e-12<order.filled_size: raise VenueError('venue reported a decreasing fill quantity',uncertain=True)
            if reported_filled+1e-9<filled: raise VenueError('venue aggregate is below immutable fill total',uncertain=True)
            price=notional/filled if filled>0 else None
            reservation_state='partially_filled' if status=='partially_filled' else 'submitted' if status in ('accepted','unknown') else status
            self.update_reservation(order.client_order_id,reservation_state,notional+fees,order.id)
            self._ledger('reconciliation',f'{order.id}:reconcile:{filled:.12f}:{notional:.12f}:{fees:.12f}',order_id=order.id,decision_id=order.decision_id,quantity=filled,notional=notional,fee=fees,payload=response)
            self.reconciliation_circuit.success()
            return {'status': status, 'filled_size': filled, 'filled_notional': notional, 'filled_fees': fees, 'average_fill_price': price, 'payload': response, 'reconciled_at': now_iso()}
        except VenueError as exc:
            self.reconciliation_circuit.failure(str(exc))
            if self.reconciliation_circuit.state().state == 'open': self.kill_switch.activate('venue reconciliation failed repeatedly', 'system')
            return {'status': 'reconciliation_required', 'reason': str(exc)}

    def cancel(self, order: OrderRecord) -> dict[str, Any]:
        if not self.venue or not order.venue_order_id: raise VenueError('missing venue order ID')
        if self.kill_switch.active() is False and not self.cancellation_circuit.allow(): raise VenueError('cancellation circuit breaker is open')
        try:
            self._record_order_attempt(order,'cancel','started')
            response = self.venue.cancel_order(order.venue_order_id)
            # A cancel acknowledgement is only an intent. Query the venue
            # again so cancel/fill races preserve the final fill quantity.
            final = self.venue.get_order(order.venue_order_id)
            final_status=_status(final.get('status', response.get('status', 'unknown')))
            final_filled=float(final.get('filled_size', final.get('size_matched', order.filled_size)) or 0)
            final_price=final.get('average_fill_price', final.get('avg_price', order.average_fill_price))
            self._ingest_fills(order, final)
            totals=self._fill_totals(order)
            immutable_filled=float(totals['quantity'] or 0)
            if final_filled > immutable_filled + 1e-9:
                raise VenueError('cancellation final state has aggregate fills without immutable fill records', uncertain=True)
            if immutable_filled + 1e-12 < order.filled_size:
                raise VenueError('cancellation final state decreased immutable fill quantity', uncertain=True)
            final_filled=immutable_filled
            final_notional=float(totals['notional'] or 0)
            final_fees=float(totals['fees'] or 0)
            self.update_reservation(order.client_order_id,final_status,final_notional+final_fees,order.id)
            self._ledger('cancellation',f'{order.id}:cancel:{final_status}:{final_filled:.12f}',order_id=order.id,decision_id=order.decision_id,quantity=final_filled,notional=final_filled*float(final_price or 0),payload={'cancel':response,'final':final})
            self._record_order_attempt(order,'cancel',final_status,response={'cancel':response,'final':final})
            self.cancellation_circuit.success(); return {'status': final_status, 'filled_size': final_filled, 'filled_notional': final_notional, 'filled_fees': final_fees, 'average_fill_price': final_notional/final_filled if final_filled else None, 'payload': {'cancel': response, 'final': final}}
        except VenueError as exc:
            self._record_order_attempt(order,'cancel','reconciliation_required',error=str(exc))
            self.cancellation_circuit.failure(str(exc)); raise

    def cancel_open_orders(self, orders):
        results=[]
        terminal={OrderStatus.FILLED.value,OrderStatus.CANCELED.value,OrderStatus.EXPIRED.value,OrderStatus.REJECTED.value,OrderStatus.FAILED.value}
        for order in orders:
            if order.status.value in terminal or not order.venue_order_id: continue
            try: results.append({'order_id':order.id,**self.cancel(order)})
            except VenueError as exc: results.append({'order_id':order.id,'status':'reconciliation_required','error':str(exc)})
        return results

    def open_orders(self) -> list[dict[str, Any]]:
        if not self.venue: return []
        try: return self.venue.list_open_orders()
        except Exception as exc:
            self.reconciliation_circuit.failure(str(exc)); raise VenueError('open-order enumeration failed; kill switch cannot be declared halted', retryable=True, uncertain=True) from exc

    def cancel_venue_order(self, venue_order_id: str) -> dict[str, Any]:
        if not self.venue: raise VenueError('venue client unavailable')
        response=self.venue.cancel_order(venue_order_id)
        final=self.venue.get_order(venue_order_id)
        return {'status':_status(final.get('status',response.get('status','unknown'))),'payload':{'cancel':response,'final':final}}
