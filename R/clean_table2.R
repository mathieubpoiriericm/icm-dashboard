# clean_table2.R
# Table 2 data cleaning function

# nolint start: object_usage_linter.
# Functions with_db_connection and clean_column_names are from utils.R
# (sourced before this file in app.R)

#' Clean Table 2 Data
#'
#' Reads and cleans the clinical_trials table from a PostgreSQL database,
#' performing column renaming, NA handling, and type conversions.
#'
#' @param con A DBI database connection object. If NULL, a new connection
#'   will be created using environment variables or defaults.
#' @param dbname Database name. Defaults to "csvd_dashboard".
#' @param host Database host. Defaults to "localhost".
#' @param port Database port. Defaults to 5432.
#' @param user Database user. Defaults to Sys.getenv("PGUSER").
#' @param password Database password. Defaults to Sys.getenv("PGPASSWORD").
#'
#' @return A cleaned data.frame ready for display.
#'
#' @export
clean_table2 <- function(
  con = NULL,
  dbname = "csvd_dashboard",
  host = "localhost",
  port = 5432,
  user = Sys.getenv("PGUSER"),
  password = Sys.getenv("PGPASSWORD")
) {
  # Load data using connection utility
  table2 <- with_db_connection(
    function(conn) {
      df <- DBI::dbGetQuery(conn, "SELECT * FROM clinical_trials")
      df$id <- NULL
      df$created_at <- NULL
      df$updated_at <- NULL
      df
    },
    con = con,
    dbname = dbname,
    host = host,
    port = port,
    user = user,
    password = password
  )

  # Clean column names using utility function
  names(table2) <- clean_column_names(names(table2))

  # Handle NA values

  table2$`Genetic Target`[is.na(table2$`Genetic Target`)] <- "(none)"

  # Convert types
  table2$`Target Sample Size` <- as.character(table2$`Target Sample Size`)

  table2
}
# nolint end: object_usage_linter.
