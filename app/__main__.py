"""Entry point: ``python -m app``.

Starting uvicorn from Python (rather than the ``uvicorn`` CLI) means host, port
and log level come from the same validated settings as everything else.
"""

import uvicorn

from app.config import get_settings
from app.main import create_app


def main() -> None:
    """Run the service with uvicorn."""
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,  # logging is configured by create_app
        access_log=False,
        # Kubernetes sends SIGTERM and waits terminationGracePeriodSeconds;
        # finish in-flight requests well inside that window.
        timeout_graceful_shutdown=20,
        server_header=False,
    )


if __name__ == "__main__":
    main()
