"""``python -m relaykit`` — or ``python path/to/relaykit/__main__.py`` (plugin wrappers run it that way)."""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from relaykit.cli import main  # type: ignore
else:
    from .cli import main

if __name__ == "__main__":
    sys.exit(main())
