"""Central logging setup. Never log API keys or file contents."""

import logging

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:  # already configured (e.g. by uvicorn) - just set level
        root.setLevel(level)
    else:
        logging.basicConfig(level=level, format=LOG_FORMAT)
    # The OpenAI/httpx libraries are chatty at INFO; keep them quiet.
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
