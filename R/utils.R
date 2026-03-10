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
  bg_color = "rgba(255, 255, 255, 0.65)",
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
    `border-radius` = "10px",
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
# CSS CLASS CONSTANTS
# =============================================================================

# CSS Class for Tooltips
# CSS class name for elements with tooltips.
# Styles are defined in www/custom.css for hover effect support.
tooltip_class <- "tooltip-box"

# CSS Class for Italic Tooltips
# CSS class name for italic elements with tooltips (e.g., gene symbols).
# Styles are defined in www/custom.css for hover effect support.
tooltip_class_italic <- "tooltip-box tooltip-box-italic"

# CSS Class for Active Filter Messages
# CSS class name for displaying active filter status.
# Styles are defined in custom.css for theme support.
filter_active_class <- "filter-message filter-active"

# CSS Class for No Active Filters
# CSS class name for displaying when no filters are active.
# Styles are defined in custom.css for theme support.
filter_none_class <- "filter-message filter-none"

# =============================================================================
# UI COMPONENT HELPERS
# =============================================================================

# Create Tip Box UI Element
#
# Creates a styled tip box with consistent formatting. Used for displaying
# helpful tips and hints to users in a visually distinct container.
#
# Args:
#   content: The tip content. Can be plain text or HTML (via shiny::HTML).
#   tip_label: The label prefix. Defaults to "Tip:".
#
# Returns:
#   A shiny div with the tip-box class.
tip_box_ui <- function(content, tip_label = "Tip:") {
  # Wrap content appropriately based on type
  formatted_content <- if (inherits(content, "shiny.tag") ||
                          inherits(content, "html")) {
    shiny::tagList(shiny::tags$strong(tip_label), " ", content)
  } else {
    shiny::HTML(paste0("<strong>", tip_label, "</strong> ", content))
  }

  shiny::div(
    class = "tip-box",
    formatted_content
  )
}

# Create Tip Row Container
#
# Creates a horizontal row containing multiple tip boxes separated by dividers.
# Used for displaying multiple related tips in a single row layout.
#
# Args:
#   ...: One or more tip_box_ui() elements or other content to include in the
#     row. Dividers are automatically inserted between elements.
#   centered: Logical. If TRUE, applies centered styling. Defaults to FALSE.
#
# Returns:
#   A shiny div with the tip-row class containing all provided elements.
tip_row_ui <- function(..., centered = FALSE) {
  elements <- list(...)
  if (length(elements) == 0L) {
    return(shiny::div(class = "tip-row"))
  }

  # Build row with dividers between elements
  row_content <- list()
  for (i in seq_along(elements)) {
    row_content <- c(row_content, list(elements[[i]]))
    if (i < length(elements)) {
      row_content <- c(row_content, list(shiny::div(class = "tip-divider")))
    }
  }

  row_class <- if (centered) "tip-row tip-row-centered" else "tip-row"

  shiny::div(
    class = row_class,
    row_content
  )
}

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

# Create Sidebar Filters Header
#
# Creates the standard "Filters" header with a horizontal rule used at the
# top of sidebar filter panels.
#
# Returns:
#   A shiny div with the sidebar-filters-header class.
sidebar_filters_header <- function() {
  shiny::div(
    class = "sidebar-filters-header",
    "Filters",
    shiny::tags$hr()
  )
}
