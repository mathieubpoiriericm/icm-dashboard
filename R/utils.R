# utils.R
# Common CSS styles and utility functions

# =============================================================================
# DATABASE UTILITIES
# =============================================================================

# Execute Function with Database Connection
#
# Manages database connection lifecycle. If a connection is provided, uses it
# directly. Otherwise, creates a new connection, executes the function, and
# ensures proper cleanup.
#
# Args:
#   fn: Function to execute. Receives the connection as its first argument.
#   con: Optional existing DBI connection. If NULL, a new connection is created.
#   dbname: Database name. Defaults to "csvd_dashboard".
#   host: Database host. Defaults to "localhost".
#   port: Database port. Defaults to 5432.
#   user: Database user. Defaults to Sys.getenv("DB_USER").
#   password: Database password. Defaults to Sys.getenv("DB_PASSWORD").
#
# Returns:
#   The result of executing fn with the connection.
with_db_connection <- function(
  fn,
  con = NULL,
  dbname = "csvd_dashboard",
  host = "localhost",
  port = 5432,
  user = Sys.getenv("DB_USER"),
  password = Sys.getenv("DB_PASSWORD")
) {
  close_con <- FALSE
  if (is.null(con)) {
    con <- tryCatch(
      DBI::dbConnect(
        RPostgres::Postgres(),
        dbname = dbname,
        host = host,
        port = port,
        user = user,
        password = password
      ),
      error = function(e) {
        stop(
          sprintf(
            "Database connection failed (host=%s, dbname=%s): %s",
            host, dbname, e$message
          ),
          call. = FALSE
        )
      }
    )
    close_con <- TRUE
  }

  on.exit({
    if (close_con && !is.null(con)) {
      tryCatch(
        DBI::dbDisconnect(con),
        error = function(e) {
          warning("Failed to close database connection: ", e$message)
        }
      )
    }
  })

  fn(con)
}

# =============================================================================
# COLUMN NAME UTILITIES
# =============================================================================

# Clean Column Names for Display
#
# Standardizes column names by replacing underscores with spaces and
# applying title case. Handles common acronyms that should remain uppercase.
#
# Args:
#   names: Character vector of column names to clean.
#   acronyms: Character vector of acronyms to preserve in uppercase.
#     Defaults to common acronyms like "GWAS", "SVD", "ID".
#
# Returns:
#   Character vector of cleaned column names.
clean_column_names <- function(
  names,
  acronyms = c("GWAS", "SVD", "ID", "Omics")
) {
  # Replace underscores with spaces

  names <- gsub("_", " ", names, fixed = TRUE)

  # Apply title case

  names <- tools::toTitleCase(names)

  # Fix acronyms that toTitleCase doesn't handle correctly

  for (acronym in acronyms) {
    pattern <- paste0("\\b", tools::toTitleCase(tolower(acronym)), "\\b")
    names <- gsub(pattern, acronym, names)
  }

  names
}

# =============================================================================
# CSS STYLE UTILITIES
# =============================================================================

# Build CSS Style String
#
# Constructs a CSS style string from a named list of properties.
# Provides a cleaner way to define inline styles.
#
# Args:
#   ...: Named arguments where names are CSS properties and values
#     are the CSS values. Use underscores for hyphenated properties
#     (e.g., font_size for font-size).
#
# Returns:
#   A single CSS style string.
build_css_style <- function(...) {
  props <- list(...)
  if (length(props) == 0L) {
    return("")
  }

  # Convert underscores to hyphens in property names

  names(props) <- gsub("_", "-", names(props))

  paste0(
    paste(names(props), unlist(props), sep = ": "),
    collapse = "; "
  )
}

# Create Box Style
#
# Creates a styled box with consistent padding, border-radius, and shadow.
#
# Args:
#   bg_color: Background color. Defaults to "#f3f4f6".
#   text_color: Text color. Defaults to "#4b5563".
#   border_color: Optional border color. If NULL, no border.
#   font_size: Font size. Defaults to "1rem".
#   padding: Padding. Defaults to "8px 12px".
#   extra_styles: Named list of additional CSS properties.
#
# Returns:
#   A CSS style string.
create_box_style <- function(
  bg_color = "#f3f4f6",
  text_color = "#4b5563",
  border_color = NULL,
  font_size = "1rem",
  padding = "8px 12px",
  extra_styles = list()
) {
  base_styles <- list(
    `background-color` = bg_color,
    color = text_color,
    `font-size` = font_size,
    padding = padding,
    `border-radius` = "20px",
    display = "inline-block",
    `box-shadow` = "0 2px 4px rgba(0, 0, 0, 0.1)"
  )

  if (!is.null(border_color)) {
    base_styles$border <- paste("1px solid", border_color)
  }

  all_styles <- c(base_styles, extra_styles)

  paste0(
    paste(names(all_styles), unlist(all_styles), sep = ": "),
    collapse = "; "
  )
}

# =============================================================================
# CSS STYLE CONSTANTS
# =============================================================================
# These styles use the create_box_style() helper for consistency.
# Common color palette:
#   - Primary: #2d287a (dark purple)
#   - Accent: #667eea (purple)
#   - Background: #f3f4f6, #f8f9fc (light grays)
#   - Text: #4b5563 (gray)
#   - Success: #008000 (green)
#   - Warning: #ff0000 (red)

# CSS Class for Tooltips
# CSS class name for elements with tooltips.
# Styles are defined in www/custom.css for hover effect support.
tooltip_class <- "tooltip-box"

# CSS Class for Italic Tooltips
# CSS class name for italic elements with tooltips (e.g., gene symbols).
# Styles are defined in www/custom.css for hover effect support.
tooltip_class_italic <- "tooltip-box tooltip-box-italic"

# CSS Style for Tip Boxes
# CSS style string for tip/info boxes in the UI.
tip_box_style <- create_box_style(
  extra_styles = list(`text-align` = "center")
)

# CSS Style for Warning Boxes
# CSS style string for warning message boxes.
warning_box_style <- create_box_style(
  bg_color = "rgba(255, 0, 0, 0.1)",
  text_color = "#ff0000",
  border_color = "#ff0000",
  font_size = "1.5rem",
  padding = "1rem 2rem",
  extra_styles = list(
    `font-weight` = "700",
    `text-align` = "center",
    border = "2px solid #ff0000"
  )
)

# CSS Style for Titles
# CSS style string for section titles.
title_style <- build_css_style(
  font_size = "2rem",
  color = "black",
  margin_bottom = "1rem",
  text_align = "center"
)

# CSS Class for Active Filter Messages
# CSS class name for displaying active filter status.
# Styles are defined in custom.css for theme support.
filter_active_class <- "filter-message filter-active"

# CSS Class for No Active Filters
# CSS class name for displaying when no filters are active.
# Styles are defined in custom.css for theme support.
filter_none_class <- "filter-message filter-none"

# Legacy style exports (kept for backwards compatibility)
# These may be removed in a future version
filter_active_style <- build_css_style(
  color = "#ff0000",
  font_size = "1rem",
  font_weight = "500",
  padding = "0.75rem",
  background_color = "#fff5f5",
  border = "1px solid #ff0000",
  border_radius = "8px",
  display = "inline-block",
  width = "auto"
)

filter_none_style <- build_css_style(
  color = "#008000",
  font_size = "1rem",
  font_weight = "500",
  padding = "0.75rem",
  background_color = "#f0fff0",
  border = "1px solid #008000",
  border_radius = "8px",
  display = "inline-block",
  width = "auto"
)

# CSS Style for About Section Header
# CSS style string for the About page header.
about_header_style <- build_css_style(
  color = "#2d287a",
  font_size = "2.0rem",
  font_weight = "700",
  margin = "0"
)

# CSS Style for About Section Text
# CSS style string for the About page text content.
about_text_style <- create_box_style(
  bg_color = "#f8f9fc",
  font_size = "1.5rem",
  padding = "2rem 3rem",
  border_color = "#e1e4e8",
  extra_styles = list(
    `margin-top` = "1rem",
    `line-height` = "1.6",
    `white-space` = "nowrap"
  )
)

# CSS Style for About Section Boxes
# CSS style string for boxes on the About page.
about_box_style <- create_box_style(
  bg_color = "#f8f9fc",
  text_color = "#2d287a",
  font_size = "1.5rem",
  padding = "1rem",
  border_color = "#e1e4e8",
  extra_styles = list(`line-height` = "1.6")
)

# =============================================================================
# UI COMPONENT HELPERS
# =============================================================================

# Create Info Card Row for About Tab
#
# Creates a consistent labeled card pattern used in the About tab.
# Reduces code duplication by consolidating the repeated card structure.
#
# Args:
#   label: The label text to display in the card.
#   content: The content to display next to the card. Can be plain text,
#     HTML content (via shiny::HTML), or any shiny tag element.
#
# Returns:
#   A shiny div containing the card and content.
about_info_card <- function(label, content) {
  shiny::div(
    class = "d-flex align-items-baseline gap-2 mb-3",
    bslib::card(
      class = "d-inline-block",
      fill = FALSE,
      bslib::card_body(
        class = "py-2 px-3 fw-bold text-primary",
        label
      )
    ),
    if (inherits(content, "shiny.tag") || inherits(content, "html")) {
      content
    } else {
      shiny::span(class = "text-body", content)
    }
  )
}
