from __future__ import annotations

from .config import ConfigurationError, Settings
from .gateway import create_server


def main() -> None:
    try:
        settings = Settings.from_env()
        server = create_server(settings)
    except ConfigurationError as exc:
        raise SystemExit(f"MIRROW production data MCP refused to start: {exc}") from exc
    server.run(transport=settings.transport)


if __name__ == "__main__":
    main()
