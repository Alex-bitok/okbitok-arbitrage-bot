import logging
import sys
from logging.handlers import RotatingFileHandler

try:
    from colorlog import ColoredFormatter
except ImportError:
    print("Install colorlog → pip install colorlog")
    sys.exit(1)

LOG_FILE = "logs/bot.log"
LOG_LEVEL = logging.INFO

from config_manager import get_config_value
ENABLE_FILE_LOGGING = get_config_value("ENABLE_FILE_LOGGING", "false").lower() == "true"

# Colored formatter for console output
formatter = ColoredFormatter(
    "%(log_color)s[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={
        'DEBUG':    'cyan',
        'INFO':     'green',
        'WARNING':  'yellow',
        'ERROR':    'red',
        'CRITICAL': 'bold_red',
    }
)

# Console handler
console_handler = logging.StreamHandler(stream=sys.stdout)
console_handler.encoding = 'utf-8'
console_handler.setFormatter(formatter)

# Main logger
logger = logging.getLogger()
logger.setLevel(LOG_LEVEL)
logger.addHandler(console_handler)

# Rotating file handler (if enabled)
if ENABLE_FILE_LOGGING:
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

logger.propagate = False  # Prevent log duplication from libraries