"""File Browser page — re-exports the production page.

The implementation is project-root scoped and has no HPC-specific
behavior, so we re-export rather than maintain a parallel copy.
"""

from __future__ import annotations

from pipeline_app.pages.file_browser import create_file_browser_page

__all__ = ["create_file_browser_page"]
