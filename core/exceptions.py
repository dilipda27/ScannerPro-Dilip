class ScannerProException(Exception):
    """Base exception for all ScannerPro custom exceptions."""
    pass

class KiteAuthError(ScannerProException):
    """Raised when Kite authentication or session retrieval fails."""
    pass

class DataFetchError(ScannerProException):
    """Raised when fetching market data from external sources fails."""
    pass

class StrategyExecutionError(ScannerProException):
    """Raised when a trading strategy encounters a fatal error during execution."""
    pass

class PaperTradeError(ScannerProException):
    """Raised when paper trading execution fails."""
    pass
