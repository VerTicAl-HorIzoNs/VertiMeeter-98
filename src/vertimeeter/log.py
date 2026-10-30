"""Standard log format: [{timestamp}] [{name}] [{type}]: {command}"""
import sys
import time
from collections import deque

NAME = "VertiMeeter"
LOG_LINES = deque(maxlen=800)

_COL = {
    "INFO": "\033[38;5;141m",
    "OK": "\033[38;5;83m",
    "WARN": "\033[38;5;214m",
    "ERROR": "\033[38;5;203m",
    "DEBUG": "\033[90m",
}


def log(kind: str, msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{NAME}] [{kind}]: {msg}"
    LOG_LINES.append(line)
    if sys.stderr.isatty():
        print(f"{_COL.get(kind, '')}{line}\033[0m", file=sys.stderr, flush=True)
    else:
        print(line, file=sys.stderr, flush=True)
