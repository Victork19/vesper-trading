import asyncio,json,os

class MarketStream:
    """Resilient market stream boundary with reconnect and sequence-gap signals."""
    def __init__(self):
        self.url=os.getenv('POLYMARKET_WS_URL','wss://ws-subscriptions-clob.polymarket.com/ws/market');self.running=False;self._stop=asyncio.Event()
    async def listen(self,token_ids:list[str],on_message):
        try:
            import websockets
            backoff=1
            while not self._stop.is_set():
                last_sequence=None
                try:
                    async with websockets.connect(self.url,open_timeout=5,ping_interval=20,ping_timeout=10,close_timeout=5) as ws:
                        await ws.send(json.dumps({'type':'market','assets_ids':token_ids}));self.running=True;backoff=1
                        async for raw in ws:
                            if self._stop.is_set():break
                            message=json.loads(raw)
                            sequence=self._sequence(message)
                            if sequence is not None and last_sequence is not None and sequence>last_sequence+1:
                                await on_message({'type':'sequence_gap','from_sequence':last_sequence,'to_sequence':sequence})
                            if sequence is not None:last_sequence=sequence
                            await on_message(message)
                except asyncio.CancelledError:raise
                except Exception as exc:
                    if not self._stop.is_set():await on_message({'type':'stream_error','error':str(exc),'retry_in_seconds':backoff})
                self.running=False
                if not self._stop.is_set():await asyncio.sleep(backoff);backoff=min(30,backoff*2)
        finally:self.running=False
    def _sequence(self,message):
        value=message.get('sequence') or message.get('seq') if isinstance(message,dict) else None
        try:return int(value) if value is not None else None
        except (TypeError,ValueError):return None
    async def stop(self):self._stop.set();self.running=False
