# clean_table2.R
# Table 2 data cleaning function

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
clean_table2 <- function(con = NULL,
                         dbname = "csvd_dashboard",
                         host = "localhost",
                         port = 5432,
                         user = Sys.getenv("PGUSER"),
                         password = Sys.getenv("PGPASSWORD")) {
  # Create connection if not provided
  close_con <- FALSE
  if (is.null(con)) {
    con <- DBI::dbConnect(
      RPostgres::Postgres(),
      dbname = dbname,
      host = host,
      port = port,
      user = user,
      password = password
    )
    close_con <- TRUE
  }

  # Load Table 2 data from the "clinical_trials" table
  table2 <- DBI::dbGetQuery(con, "SELECT * FROM clinical_trials")
  table2$id <- NULL
  table2$created_at <- NULL
  table2$updated_at <- NULL

  # Close connection if we created it
  if (close_con) {
    DBI::dbDisconnect(con)
  }

  # Clean column names
  names(table2) <- gsub("_", " ", names(table2), fixed = TRUE)
  names(table2) <- tools::toTitleCase(names(table2))
  names(table2) <- gsub("Svd", "SVD", names(table2), fixed = TRUE)
  names(table2) <- gsub("Registry Id", "Registry ID", names(table2), fixed = TRUE)

  # Handle NA values

  table2$`Genetic Target`[is.na(table2$`Genetic Target`)] <- "(none)"

  # Convert types
  table2$`Target Sample Size` <- as.character(table2$`Target Sample Size`)

  table2
}
