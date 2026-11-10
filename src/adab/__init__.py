"""data-agent-gauntlet: benchmark AI coding agents on real AWS data tasks."""

__version__ = "0.1.1"

# Every statement the harness sends to Redshift starts with this tag, so the
# harness can tell its own queries apart from the agent's in sys_query_history.
HARNESS_TAG = "/* adab-harness */ "
