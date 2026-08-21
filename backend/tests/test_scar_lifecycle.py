from app.engines import ScarEngine
from app.models import DecisionRecord, Mode


class MemoryStub:
    def __init__(self):
        self.items = {}
        self.events = []

    def put(self, tier, key, value):
        if tier == 'WARM':
            self.items[key] = value

    def scars(self):
        from app.models import Scar
        return [Scar.model_validate(value) for value in self.items.values() if 'lesson' in value]

    def event(self, name, payload):
        self.events.append((name, payload))


def decision(market_id, pnl=0):
    return DecisionRecord(
        id=f'decision-{market_id}', mode=Mode.PAPER, market_id=market_id,
        strategy_id='reference_class', market_type='politics', regime='baseline',
        action='BUY', side='YES', size=.02, price=.4, fair_probability=.6,
        confidence=.8, risk_score=5, edge=.2, rationale='test', pnl=pnl,
    )


def test_scar_rehabilitation_requires_qualifying_outcomes():
    memory = MemoryStub()
    scars = ScarEngine(memory)
    scar, _ = scars.failure(decision('loss-market', pnl=-.5), failure_type='negative_outcome', process_score=0)

    assert scar.status == 'active'
    assert scar.rehabilitation_progress == 0
    assert scar.cooldown_until
    assert scar.impact.max_size_multiplier < 1

    positive = decision('recovery-market')
    scars.rehabilitate(positive, clv=0)
    scars.rehabilitate(positive, clv=0)
    scars.rehabilitate(positive, clv=0)

    recovered = memory.scars()[0]
    assert recovered.status == 'rehabilitated'
    assert recovered.rehabilitation_progress == recovered.rehabilitation_required
    assert recovered.resolved_at
    assert recovered.impact.max_size_multiplier == 1


def test_repeated_failure_reinforces_one_contextual_scar():
    memory = MemoryStub()
    scars = ScarEngine(memory)
    first, _ = scars.failure(decision('same-market', pnl=-.5), failure_type='negative_outcome', process_score=0)
    second, _ = scars.failure(decision('same-market', pnl=-.2), failure_type='negative_outcome', process_score=0)
    assert second.id == first.id
    assert second.evidence_count == 2
    assert second.context['market_id'] == 'same-market'
