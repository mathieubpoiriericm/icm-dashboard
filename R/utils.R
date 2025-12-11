# utils.R
# Common CSS styles and utility functions

#' CSS Style for Tooltips
#'
#' Common CSS style string for elements with tooltips.
#'
#' @export
tooltip_style <- paste0(
  "border-bottom: 1px dotted #667eea; cursor: help; ",
  "background-color: #f3f4f6; padding: 4px 8px; ",
  "border-radius: 20px; display: inline-block; ",
  "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);"
)

#' CSS Style for Italic Tooltips
#'
#' CSS style string for italic elements with tooltips (e.g., gene symbols).
#'
#' @export
tooltip_style_italic <- paste0(
  "border-bottom: 1px dotted #667eea; font-style: italic; cursor: help; ",
  "background-color: #f3f4f6; padding: 4px 8px; ",
  "border-radius: 20px; display: inline-block; ",
  "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);"
)

#' CSS Style for Tip Boxes
#'
#' CSS style string for tip/info boxes in the UI.
#'
#' @export
tip_box_style <- paste0(
  "background-color: #f3f4f6; padding: 8px 12px; ",
  "border-radius: 20px; display: inline-block; font-size: 1rem; ",
  "color: #4b5563; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); ",
  "text-align: center;"
)

#' CSS Style for Warning Boxes
#'
#' CSS style string for warning message boxes.
#'
#' @export
warning_box_style <- paste0(
  "font-size: 1.5rem; font-weight: 700; color: #ff0000; ",
  "text-align: center; padding: 1rem 2rem; ",
  "border: 2px solid #ff0000; border-radius: 20px; ",
  "background: rgba(255, 0, 0, 0.1); display: inline-block; ",
  "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);"
)

#' CSS Style for Titles
#'
#' CSS style string for section titles.
#'
#' @export
title_style <- paste0(
  "font-size: 2rem; color: black; margin-bottom: 1rem; text-align: center;"
)

#' CSS Style for Active Filter Messages
#'
#' CSS style string for displaying active filter status.
#'
#' @export
filter_active_style <- paste0(
  "color: #ff0000; font-size: 1rem; font-weight: 500; padding: 0.75rem; ",
  "background-color: #fff5f5; border: 1px solid #ff0000; ",
  "border-radius: 8px; display: inline-block; width: auto;"
)

#' CSS Style for No Active Filters
#'
#' CSS style string for displaying when no filters are active.
#'
#' @export
filter_none_style <- paste0(
  "color: #008000; font-size: 1rem; font-weight: 500; padding: 0.75rem; ",
  "background-color: #f0fff0; border: 1px solid #008000; ",
  "border-radius: 8px; display: inline-block; width: auto;"
)

#' CSS Style for About Section Header
#'
#' CSS style string for the About page header.
#'
#' @export
about_header_style <- paste0(
  "color: #2d287a; font-size: 2.0rem; font-weight: 700; margin: 0;"
)

#' CSS Style for About Section Text
#'
#' CSS style string for the About page text content.
#'
#' @export
about_text_style <- paste0(
  "display: inline-block; padding: 2rem 3rem; color: #4b5563; ",
  "font-size: 1.5rem; margin-top: 1rem; line-height: 1.6; ",
  "background-color: #f8f9fc; border-radius: 20px; ",
  "border: 1px solid #e1e4e8; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); ",
  "white-space: nowrap;"
)

#' CSS Style for About Section Boxes
#'
#' CSS style string for boxes on the About page.
#'
#' @export
about_box_style <- paste0(
  "display: inline-block; padding: 1rem; color: #2d287a; ",
  "font-size: 1.5rem; line-height: 1.6; background-color: #f8f9fc; ",
  "border-radius: 20px; border: 1px solid #e1e4e8; ",
  "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);"
)
