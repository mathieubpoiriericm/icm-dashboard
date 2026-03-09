"""Blinker signals for pipeline event decoupling.

ETL modules emit these signals; the notification module subscribes.
Neither side imports the other, keeping coupling loose.
"""

from __future__ import annotations

from blinker import signal

# Emitted at the start of any pipeline mode.
# kwargs: mode (str)
pipeline_started = signal("pipeline-started")

# Emitted after a successful pipeline run.
# kwargs: run_data (PipelineRunData), mode (str), error (None)
pipeline_completed = signal("pipeline-completed")

# Emitted when a pipeline run fails with an unhandled exception.
# kwargs: error (str), mode (str)
pipeline_failed = signal("pipeline-failed")

# Emitted after external data sync completes.
# kwargs: summary (str)
external_sync_completed = signal("external-sync-completed")
