"""Results Viewer page — re-exports the production page.

The HPC stack reads the same pipeline JSON reports as ``pipeline_app/``.
The implementation has no HPC-specific behavior, so we re-export the
production module rather than maintain a parallel copy.
"""

from __future__ import annotations

from pipeline_app.pages.results_viewer import (
    create_results_viewer_page,
    is_safe_report_id,
)

__all__ = ["create_results_viewer_page", "is_safe_report_id"]
