import os

def fast_markets_only():
    return os.getenv('FAST_MARKETS_ONLY','true').lower()=='true'

def fast_max_resolution_hours():
    return max(.01,float(os.getenv('AUTO_PAPER_FAST_MAX_RESOLUTION_HOURS','1')))

def fast_market_allowed(resolution_hours:float,source:str='polymarket-clob'):
    if not fast_markets_only() or not source.startswith('polymarket'):
        return True
    return resolution_hours<=fast_max_resolution_hours()
