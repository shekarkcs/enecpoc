import os
from pathlib import Path

DATA_DIR = Path(os.getenv('DATA_DIR', './data'))
AUTH_MODE = os.getenv('AUTH_MODE', 'entra')
SAP_MODE = os.getenv('SAP_MODE', 'mock')
COMPANY_CODE = os.getenv('COMPANY_CODE', '1000')
ACCOUNTS = set(os.getenv('ALLOWED_GL_ACCOUNTS', '400100,210000,660000').split(','))
COST_CENTRES = set(os.getenv('ALLOWED_COST_CENTRES', 'CC100,CC200').split(','))
