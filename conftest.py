import sys
from pathlib import Path

APP_DIR = Path(__file__).parent / "app"

if APP_DIR.is_dir():
    for agent_dir in APP_DIR.iterdir():
        if agent_dir.is_dir():
            sys.path.insert(0, str(agent_dir))

WEBHOOK_HANDLER_DIR = Path(__file__).parent / "lambda" / "webhook_handler"

if WEBHOOK_HANDLER_DIR.is_dir():
    sys.path.insert(0, str(WEBHOOK_HANDLER_DIR))
