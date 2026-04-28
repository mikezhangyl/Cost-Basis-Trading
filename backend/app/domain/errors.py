from enum import StrEnum


class DataErrorCode(StrEnum):
    MISSING_TOKEN = "MISSING_TOKEN"
    NO_PERMISSION = "NO_PERMISSION"
    EMPTY_DATA = "EMPTY_DATA"
    PARTIAL_DATA = "PARTIAL_DATA"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_SYMBOL = "INVALID_SYMBOL"


class DataUnavailableError(Exception):
    def __init__(self, code: DataErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
