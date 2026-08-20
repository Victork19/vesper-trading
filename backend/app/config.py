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
    auth_required:bool=os.getenv('VESPER_AUTH_REQUIRED','true').lower()=='true'
settings=Settings()
