import json
from pathlib import Path
from .logger import get_logger

logger = get_logger(__name__)

def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        return {}
        
    try:
        with open(path, "r") as f:
            config = json.load(f)
            logger.debug(f"Loaded configuration from {config_path}")
            return config
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON config at {config_path}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error reading {config_path}: {e}")
        return {}
