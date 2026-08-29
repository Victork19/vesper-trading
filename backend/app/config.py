import os
from urllib.parse import urlparse
from pydantic import BaseModel, Field
class Settings(BaseModel):
    deployment_stage:str=os.getenv('VESPER_DEPLOYMENT_STAGE','paper')
    canary_max_capital:float=float(os.getenv('CANARY_MAX_CAPITAL','0'))
    mode:str=os.getenv('TRADING_MODE','paper')
    # Phase 0 hard lock: live execution requires an explicit release token in
    # addition to the ordinary feature flag. The default is permanently safe.
    live_hard_lock:bool=os.getenv('VESPER_LIVE_HARD_LOCK','true').lower()=='true'
    live_enabled:bool=(os.getenv('LIVE_TRADING_ENABLED','false').lower()=='true' and os.getenv('VESPER_LIVE_HARD_LOCK','true').lower()=='false')
    polymarket_chain_id:int=int(os.getenv('POLYMARKET_CHAIN_ID','137'))
    signer_address:str=os.getenv('POLYMARKET_SIGNER_ADDRESS','')
    funder_address:str=os.getenv('POLYMARKET_FUNDER_ADDRESS','')
    max_capital:float=float(os.getenv('MAX_LIVE_CAPITAL','0'))
    max_order_size:float=float(os.getenv('MAX_LIVE_ORDER_SIZE','0'))
    max_portfolio_heat:float=float(os.getenv('MAX_PORTFOLIO_HEAT','.20'))
    min_sample:int=int(os.getenv('MIN_LIVE_SAMPLE','100'))
    require_operator_approval:bool=True
    operator_approval_code:str=os.getenv('OPERATOR_APPROVAL_CODE','')
    second_operator_approval_code:str=os.getenv('SECOND_OPERATOR_APPROVAL_CODE','')
    require_dual_live_approval:bool=os.getenv('VESPER_REQUIRE_DUAL_LIVE_APPROVAL','true').lower()=='true'
    min_data_quality:float=float(os.getenv('MIN_DATA_QUALITY','.95'))
    api_key:str=os.getenv('VESPER_API_KEY','')
    admin_key:str=os.getenv('VESPER_ADMIN_KEY','')
    session_secret:str=os.getenv('VESPER_SESSION_SECRET','')
    session_ttl_seconds:int=int(os.getenv('VESPER_SESSION_TTL_SECONDS','28800'))
    privileged_session_ttl_seconds:int=int(os.getenv('VESPER_PRIVILEGED_SESSION_TTL_SECONDS','900'))
    session_idle_seconds:int=int(os.getenv('VESPER_SESSION_IDLE_SECONDS','900'))
    cookie_samesite:str=os.getenv('VESPER_COOKIE_SAMESITE','lax')
    cookie_secure:bool=os.getenv('VESPER_COOKIE_SECURE','false').lower()=='true'
    auth_required:bool=os.getenv('VESPER_AUTH_REQUIRED','true').lower()=='true'
settings=Settings()

def validate_runtime_config():
    """Reject unsafe configuration before a production process accepts traffic."""
    if settings.deployment_stage not in {'paper','shadow','controlled','canary','production'}:
        raise RuntimeError('VESPER_DEPLOYMENT_STAGE is invalid.')
    if os.getenv('VESPER_ENV','development').lower() not in {'production','prod'}:
        return
    if not os.getenv('DATABASE_URL','').strip():
        raise RuntimeError('DATABASE_URL is required in production.')
    if not settings.auth_required:
        raise RuntimeError('VESPER_AUTH_REQUIRED=true is mandatory in production.')
    for name,value in (('VESPER_API_KEY',settings.api_key),('VESPER_ADMIN_KEY',settings.admin_key),('VESPER_SESSION_SECRET',settings.session_secret)):
        if len(value)<32 or value.startswith('replace-with-'):
            raise RuntimeError(f'{name} must be a random secret of at least 32 characters in production.')
    origins=[item.strip().rstrip('/') for item in os.getenv('CORS_ORIGINS','').split(',') if item.strip()]
    parsed_origins=[urlparse(item) for item in origins]
    if (not origins or '*' in origins or any(
        parsed.scheme!='https' or not parsed.netloc or parsed.path not in ('','/') or
        'localhost' in parsed.hostname.lower() or parsed.hostname in {'127.0.0.1','::1'}
        for parsed in parsed_origins
    )):
        raise RuntimeError('CORS_ORIGINS must contain only explicit HTTPS production origins.')
    if settings.cookie_secure is not True:
        raise RuntimeError('VESPER_COOKIE_SECURE=true is required in production.')
    if settings.live_hard_lock and settings.live_enabled:
        raise RuntimeError('Phase 0 live hard lock is active; live execution cannot be enabled.')
    if settings.live_enabled and settings.deployment_stage not in {'controlled','canary','production'}:
        raise RuntimeError('Live execution requires controlled, canary, or production deployment stage.')
    if settings.live_enabled and (settings.max_capital<=0 or settings.max_order_size<=0):
        raise RuntimeError('Live mode requires positive capital and order limits.')
    if settings.deployment_stage=='canary' and (settings.canary_max_capital<=0 or settings.canary_max_capital>settings.max_capital):
        raise RuntimeError('Canary deployment requires a positive CANARY_MAX_CAPITAL no greater than MAX_LIVE_CAPITAL.')
    if settings.live_enabled and (len(settings.operator_approval_code)<32 or settings.operator_approval_code.startswith('replace-with-')):
        raise RuntimeError('OPERATOR_APPROVAL_CODE must be a random secret of at least 32 characters when live trading is enabled.')
    if settings.live_enabled and settings.require_dual_live_approval and (len(settings.second_operator_approval_code)<32 or settings.second_operator_approval_code.startswith('replace-with-')):
        raise RuntimeError('SECOND_OPERATOR_APPROVAL_CODE must be a distinct random secret of at least 32 characters when dual live approval is enabled.')
    if settings.privileged_session_ttl_seconds<60 or settings.session_idle_seconds<60:
        raise RuntimeError('Privileged session TTL and idle timeout must each be at least 60 seconds.')
    if settings.live_enabled and settings.polymarket_chain_id != 137:
        raise RuntimeError('POLYMARKET_CHAIN_ID must be 137 for live Polymarket execution.')
    if settings.live_enabled and (not settings.signer_address or not settings.funder_address):
        raise RuntimeError('POLYMARKET_SIGNER_ADDRESS and POLYMARKET_FUNDER_ADDRESS are required for live execution.')
