import os
from dotenv import load_dotenv

# Load .env into environment
load_dotenv()

def get_config_value(key: str, default=None):
    return os.getenv(key, default)
