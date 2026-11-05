from __future__ import annotations

import logging

NOISY = ("httpx", "httpcore", "uvicorn.access", "asyncio", "PIL")


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    # httpx logs full request URLs at INFO: the ZPay re-query URL carries the merchant key.
    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
