import os
from pydantic import BaseModel, Field
class Settings(BaseModel):
    mode:str=os.getenv('TRADING_MODE','paper')
    live_enabled:bool=os.getenv('LIVE_TRADING_ENABLED','false').lower()=='true'
    max_capital:float=float(os.getenv('MAX_LIVE_CAPITAL','0'))
    max_order_size:float=float(os.getenv('MAX_LIVE_ORDER_SIZE','0'))
    max_portfolio_heat:float=float(os.getenv('MAX_PORTFOLIO_HEAT','.20'))
    min_sample:int=int(os.getenv('MIN_LIVE_SAMPLE','100'))
    require_operator_approval:bool=True
    operator_approval_code:str=os.getenv('OPERATOR_APPROVAL_CODE','')
    min_data_quality:float=float(os.getenv('MIN_DATA_QUALITY','.95'))
    api_key:str=os.getenv('VESPER_API_KEY','')
    admin_key:str=os.getenv('VESPER_ADMIN_KEY','')
    session_secret:str=os.getenv('VESPER_SESSION_SECRET','')
    session_ttl_seconds:int=int(os.getenv('VESPER_SESSION_TTL_SECONDS','28800'))
    cookie_samesite:str=os.getenv('VESPER_COOKIE_SAMESITE','lax')
    cookie_secure:bool=os.getenv('VESPER_COOKIE_SECURE','false').lower()=='true'
    auth_required:bool=os.getenv('VESPER_AUTH_REQUIRED','true').lower()=='true'
settings=Settings()

def validate_runtime_config():
    """Reject unsafe configuration before a production process accepts traffic."""
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
    if not origins or '*' in origins or any('localhost' in item or '127.0.0.1' in item for item in origins):
        raise RuntimeError('CORS_ORIGINS must contain only explicit HTTPS production origins.')
    if settings.cookie_secure is not True:
        raise RuntimeError('VESPER_COOKIE_SECURE=true is required in production.')
    if settings.live_enabled and (settings.max_capital<=0 or settings.max_order_size<=0):
        raise RuntimeError('Live mode requires positive capital and order limits.')
    if settings.live_enabled and (len(settings.operator_approval_code)<32 or settings.operator_approval_code.startswith('replace-with-')):
        raise RuntimeError('OPERATOR_APPROVAL_CODE must be a random secret of at least 32 characters when live trading is enabled.')
