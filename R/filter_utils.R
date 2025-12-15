# filter_utils.R
# Unified filter utilities for data.table filtering

# nolint start: object_usage_linter.
# filter_active_style and filter_none_style are from utils.R
# (sourced before this file in app.R)

#' Apply Column Filter
#'
#' A generic filter function that filters a data.table by column value.
#' Consolidates the common filtering pattern used across the application.
#'
#' @param dt data.table. The data to filter.
#' @param column Character. The column name to filter on.
#' @param filter_value Character vector. Values to include.
#' @param exclude_all Logical. If TRUE, skip filtering when "all" is selected.
#'   Defaults to TRUE.
#' @param match_type Character. Type of matching: "exact", "prefix", or "regex".
#'   Defaults to "exact".
#'
#' @return Filtered data.table.
#'
#' @examples
#' \dontrun{
#' dt <- apply_column_filter(dt, "Phase", c("Phase 1", "Phase 2"))
#' dt <- apply_column_filter(
#'   dt, "Sponsor Type", "Industry", match_type = "prefix"
#' )
#' }
#'
#' @keywords internal
apply_column_filter <- function(
  dt,
  column,
  filter_value,
  exclude_all = TRUE,
  match_type = "exact"
) {
  # Skip if no filter value
  if (is.null(filter_value) || length(filter_value) == 0L) {
    return(dt)
  }

  # Skip if "all" is selected and exclude_all is TRUE
  if (exclude_all && "all" %in% filter_value) {
    return(dt)
  }

  # Apply filter based on match type
  switch(
    match_type,
    exact = dt[get(column) %in% filter_value],
    prefix = dt[grepl(paste0("^", filter_value, collapse = "|"), get(column))],
    regex = dt[grepl(paste(filter_value, collapse = "|"), get(column))],
    dt # Default: return unfiltered
  )
}

#' Apply Index-Based Filter
#'
#' Filters a data.table using pre-computed row indices from a fastmap.
#' Optimized for O(1) lookups on large datasets.
#'
#' @param dt data.table. The data to filter.
#' @param filter_value Character vector. Values to look up in the index.
#' @param index_map fastmap object. Pre-computed mapping of values to row
#'   indices.
#' @param row_id_column Character. Name of the row ID column. Defaults to
#'   "original_row_num".
#' @param exclude_all Logical. If TRUE, skip filtering when "all" is selected.
#'   Defaults to TRUE.
#'
#' @return Filtered data.table.
#'
#' @keywords internal
apply_index_filter <- function(
  dt,
  filter_value,
  index_map,
  row_id_column = "original_row_num",
  exclude_all = TRUE
) {
  # Skip if no filter value
  if (is.null(filter_value) || length(filter_value) == 0L) {
    return(dt)
  }

  # Skip if "all" is selected and exclude_all is TRUE
  if (exclude_all && "all" %in% filter_value) {
    return(dt)
  }

  # Get matching rows from index
  matching_rows <- unique(unlist(index_map$mget(filter_value)))
  dt[get(row_id_column) %in% matching_rows]
}

#' Apply Range Filter
#'
#' Filters a data.table by a numeric range.
#'
#' @param dt data.table. The data to filter.
#' @param column Character. The column name to filter on.
#' @param range_value Numeric vector of length 2. c(min, max) values.
#' @param default_min Numeric. Default minimum value. If range matches default,
#'   filter is skipped. Defaults to NULL (no skip).
#' @param default_max Numeric. Default maximum value. If range matches default,
#'   filter is skipped. Defaults to NULL (no skip).
#'
#' @return Filtered data.table.
#'
#' @keywords internal
apply_range_filter <- function(
  dt,
  column,
  range_value,
  default_min = NULL,
  default_max = NULL
) {
  # Skip if no filter value
  if (is.null(range_value)) {
    return(dt)
  }

  # Skip if at default values
  if (
    !is.null(default_min) &&
      !is.null(default_max) &&
      range_value[1L] == default_min &&
      range_value[2L] == default_max
  ) {
    return(dt)
  }

  dt[get(column) >= range_value[1L] & get(column) <= range_value[2L]]
}

#' Apply Sponsor Type Filter
#'
#' Special filter for sponsor types that handles the Academic/Industry logic.
#'
#' @param dt data.table. The data to filter.
#' @param filter_value Character vector. Sponsor type values.
#'
#' @return Filtered data.table.
#'
#' @keywords internal
apply_sponsor_type_filter <- function(dt, filter_value) {
  # Skip if no filter value or "all" selected
  if (
    is.null(filter_value) ||
      length(filter_value) == 0L ||
      "all" %in% filter_value
  ) {
    return(dt)
  }

  # Both selected = keep all
  if ("Academic" %in% filter_value && "Industry" %in% filter_value) {
    return(dt)
  }

  # Single selection
  if ("Academic" %in% filter_value) {
    return(dt[get("Sponsor Type") == "Academic"])
  }

  # Industry (matches "Industry" prefix)
  dt[grepl("^Industry", get("Sponsor Type"))]
}

#' Apply Single-Value Filter
#'
#' Filter that only applies when exactly one value is selected.
#' Used for binary filters like Mendelian Randomization.
#'
#' @param dt data.table. The data to filter.
#' @param column Character. The column name to filter on.
#' @param filter_value Character vector. Filter values.
#'
#' @return Filtered data.table.
#'
#' @keywords internal
apply_single_value_filter <- function(dt, column, filter_value) {
  # Only apply when exactly one value is selected
  if (!is.null(filter_value) && length(filter_value) == 1L) {
    return(dt[get(column) %in% filter_value])
  }
  dt
}

# =============================================================================
# FILTER MESSAGE UTILITIES
# =============================================================================

#' Build Filter List from Filter Specifications
#'
#' Helper function to build a character vector of active filter descriptions.
#' Used by both Table 1 and Table 2 filter message rendering.
#'
#' @param filter_specs List of lists, each containing:
#'   - name: Display name for the filter
#'   - value: Current filter value(s)
#'   - is_single: If TRUE, only show when exactly one value selected
#'   - exclude_all: If TRUE, exclude "all" from display
#'
#' @return Character vector of filter descriptions.
#'
#' @export
build_filter_list <- function(filter_specs) {
  filters_applied <- character(0L)

  for (spec in filter_specs) {
    value <- spec$value
    name <- spec$name
    is_single <- isTRUE(spec$is_single)
    exclude_all <- isTRUE(spec$exclude_all)

    if (is.null(value) || length(value) == 0L) {
      next
    }

    if (is_single && length(value) != 1L) {
      next
    }

    if (exclude_all && "all" %in% value) {
      next
    }

    filters_applied <- c(
      filters_applied,
      paste0(name, ": ", paste(value, collapse = ", "))
    )
  }

  filters_applied
}

#' Render Filter Message HTML
#'
#' Creates the styled div for displaying active filter status.
#' Used by both Table 1 and Table 2 filter message rendering.
#'
#' @param filters_applied Character vector of filter descriptions.
#'
#' @return A Shiny div element.
#'
#' @export
render_filter_message <- function(filters_applied) {
  if (length(filters_applied) > 0L) {
    shiny::div(
      style = filter_active_style,
      shiny::HTML(paste0(
        "<strong>Active Filters:</strong> ",
        paste(filters_applied, collapse = " | ")
      ))
    )
  } else {
    shiny::div(
      style = filter_none_style,
      shiny::HTML("<strong>Active Filters:</strong> None")
    )
  }
}
# nolint end: object_usage_linter.
