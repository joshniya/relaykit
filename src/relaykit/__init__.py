"""relaykit: run any AI coding agent on a project too big for one context window.

A relay is a chain of fresh agent sessions. Each session works until its context reaches a set
point, writes a handoff into a progress file, and ends with a marker line. The supervisor then
starts the next session, which picks up from that file. The chain ends when the plan is done.
"""

__version__ = "0.1.0"
