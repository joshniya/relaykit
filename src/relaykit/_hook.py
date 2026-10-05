"""Hook entry point: ``python <path>/_hook.py <agent>``.

Run by the agent CLI as a separate process, possibly with a different working directory and no
relaykit on sys.path, so it puts its own package on the path first. It must stay importable with
nothing but the standard library."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    try:
        from relaykit.hooks import main
        sys.exit(main(sys.argv))
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
