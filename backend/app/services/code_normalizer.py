import re

from app.domain.errors import DataErrorCode, DataUnavailableError

TS_CODE_PATTERN = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
BARE_CODE_PATTERN = re.compile(r"^\d{6}$")


def normalize_ts_code(raw_code: str) -> str:
    code = raw_code.strip().upper()
    if TS_CODE_PATTERN.match(code):
        return code
    if not BARE_CODE_PATTERN.match(code):
        raise DataUnavailableError(DataErrorCode.INVALID_SYMBOL, f"Invalid stock code: {raw_code}")
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "1", "2", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    raise DataUnavailableError(DataErrorCode.INVALID_SYMBOL, f"Cannot infer exchange for stock code: {raw_code}")
