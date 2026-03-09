# clean_table2.R
# Table 2 data cleaning function

# nolint start: object_usage_linter.
# Functions with_db_connection and clean_column_names are from utils.R
# (sourced before this file in app.R)

# Clean Table 2 Data
#
# Reads and cleans the clinical_trials table from a PostgreSQL database,
# performing column renaming, NA handling, and type conversions.
#
# Args:
#   con: A DBI database connection object. If NULL, a new connection
#     will be created using environment variables or defaults.
#   dbname: Database name. Defaults to "csvd_dashboard".
#   host: Database host. Defaults to "localhost".
#   port: Database port. Defaults to 5432.
#   user: Database user. Defaults to Sys.getenv("DB_USER").
#   password: Database password. Defaults to Sys.getenv("DB_PASSWORD").
#
# Returns:
#   A cleaned data.frame ready for display.
clean_table2 <- function(
  con = NULL,
  dbname = "csvd_dashboard",
  host = "localhost",
  port = 5432,
  user = Sys.getenv("DB_USER"),
  password = Sys.getenv("DB_PASSWORD")
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
