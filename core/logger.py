import logging
import sys
from pathlib import Path

def setup_logger(name: str = "scanner_pro", log_file: str = "scheduler.log") -> logging.Logger:
    """
    Configure and return a centralized logger with standard formatting.
    Writes to both console and the specified log_file.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Prevent duplicate handlers if called multiple times
    if logger.hasHandlers():
        logger.handlers.clear()
        
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Console Handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    # File Handler
    try:
        log_path = Path(log_file)
        # Ensure directory exists if path is not just filename
        if log_path.parent != Path('.'):
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception as e:
        logger.warning(f"Failed to setup file handler for logger: {e}")
        
    return logger

# Create a default global logger instance
logger = setup_logger()
