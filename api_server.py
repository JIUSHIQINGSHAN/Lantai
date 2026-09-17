"""Thin entrypoint shim — prefers console script `lantai-server` when installed."""

from lantai.api.app import main

if __name__ == "__main__":
    main()
