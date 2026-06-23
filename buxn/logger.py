import logging
import sys

def setup_logger(verbose: bool = False):
    logger = logging.getLogger("buxn")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="[%(levelname)s] %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger

def get_logger(name: str):
    # Extracts just the module name, e.g. "cli" instead of "__main__"
    mod_name = name.split(".")[-1] if "." in name else name
    return logging.getLogger(f"buxn.{mod_name}")
