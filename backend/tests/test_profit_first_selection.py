from types import SimpleNamespace

from app.worker import _select_strategy


class Memory:
    def __init__(self, decisions):
        self._decisions = decisions

    def decisions(self):
        return self._decisions


def decision(strategy, pnl, outcome='win'):
    return SimpleNamespace(
        strategy_id=strategy,
        research_eligible=True,
        outcome=outcome,
        pnl=pnl,
        executed_size=0,
        execution_reconciled=False,
        size=.01,
        paper_fill_fraction=1,
        paper_execution_price=.5,
        executable_price=.5,
        price=.5,
        executed_average_price=None,
        executed_fees=0,
    )


def test_strategy_selection_prefers_positive_resolved_expectancy(monkeypatch):
    monkeypatch.setenv('PAPER_STRATEGY_MIN_RESOLVED', '3')
    decisions = [decision('reference_class', .01) for _ in range(3)]
    decisions += [decision('fast_model', -.01, 'loss') for _ in range(3)]
    selected, _ = _select_strategy(Memory(decisions), 'market-1', ['reference_class', 'fast_model'])
    assert selected == 'reference_class'


def test_strategy_selection_stops_when_all_mature_strategies_are_negative(monkeypatch):
    monkeypatch.setenv('PAPER_STRATEGY_MIN_RESOLVED', '3')
    decisions = [decision('reference_class', -.01, 'loss') for _ in range(3)]
    decisions += [decision('fast_model', -.02, 'loss') for _ in range(3)]
    selected, _ = _select_strategy(Memory(decisions), 'market-2', ['reference_class', 'fast_model'])
    assert selected is None
