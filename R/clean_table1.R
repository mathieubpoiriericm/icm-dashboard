# clean_table1.R
# Table 1 data cleaning function

# nolint start: object_usage_linter.
# Functions with_db_connection and clean_column_names are from utils.R
# (sourced before this file in app.R)

#' Clean Table 1 Data
#'
#' Reads and cleans the genes table from a PostgreSQL database, performing
#' various data transformations including column renaming, NA handling,
#' and text cleanup.
#'
#' @param con A DBI database connection object. If NULL, a new connection
#'   will be created using environment variables or defaults.
#' @param dbname Database name. Defaults to "csvd_dashboard".
#' @param host Database host. Defaults to "localhost".
#' @param port Database port. Defaults to 5432.
#' @param user Database user. Defaults to Sys.getenv("DB_USER").
#' @param password Database password. Defaults to Sys.getenv("DB_PASSWORD").
#'
#' @return A cleaned data.frame with processed columns ready for display.
#'
#' @export
clean_table1 <- function(
  con = NULL,
  dbname = "csvd_dashboard",
  host = "localhost",
  port = 5432,
  user = Sys.getenv("DB_USER"),
  password = Sys.getenv("DB_PASSWORD")
) {
  # Load data using connection utility
  table1 <- with_db_connection(
    function(conn) {
      df <- DBI::dbGetQuery(conn, "SELECT * FROM genes")
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

  # Replace empty cells with NA
  table1[!nzchar(table1, keepNA = TRUE)] <- NA
  table1[table1 == " "] <- NA

  # Replace NA in `Link to Monogenetic Disease` column by char N
  table1$mendelian_randomization[is.na(table1$mendelian_randomization)] <- "N"

  # Reorder and clean column names
  table1 <- table1 |>
    dplyr::select("gene", dplyr::everything())

  names(table1) <- clean_column_names(names(table1))

  # Convert multiple comma-separated strings in a cell into a list
  # of strings (GWAS Trait)
  table1$`GWAS Trait`[is.na(table1$`GWAS Trait`)] <- "(none found)"
  table1$`GWAS Trait` <- strsplit(table1$`GWAS Trait`, ", ", fixed = TRUE)

  # Clean up "Evidence from Other Omics Studies" column
  table1$`Evidence from Other Omics Studies` <- gsub(
    "*",
    "",
    table1$`Evidence from Other Omics Studies`,
    fixed = TRUE
  )
  table1$`Evidence from Other Omics Studies` <- gsub(
    ";(?=[^[:alnum:]]|$)",
    "",
    table1$`Evidence from Other Omics Studies`,
    perl = TRUE
  )
  table1$`Evidence from Other Omics Studies` <- gsub(
    ";",
    ", ",
    table1$`Evidence from Other Omics Studies`,
    fixed = TRUE
  )
  table1$`Evidence from Other Omics Studies` <- gsub(
    ":",
    ";",
    table1$`Evidence from Other Omics Studies`,
    fixed = TRUE
  )
  table1$`Evidence from Other Omics Studies` <- gsub(
    paste0(
      "Evidence for causal implication from ML-based ",
      "functional prediction (MENTR)"
    ),
    "mutation effect prediction on ncRNA transcription",
    table1$`Evidence from Other Omics Studies`,
    fixed = TRUE
  )
  table1$`Evidence from Other Omics Studies` <- gsub(
    "YFS.BLOOD.RNAARR",
    "",
    table1$`Evidence from Other Omics Studies`,
    fixed = TRUE
  )
  table1$`Evidence from Other Omics Studies` <- gsub(
    "GTEX - Cross-tissue sCCA3",
    "cross-tissue",
    table1$`Evidence from Other Omics Studies`,
    fixed = TRUE
  )
  table1$`Evidence from Other Omics Studies` <- gsub(
    "GTEx.",
    "",
    table1$`Evidence from Other Omics Studies`,
    fixed = TRUE
  )
  table1$`Evidence from Other Omics Studies` <- gsub(
    "_",
    " ",
    table1$`Evidence from Other Omics Studies`,
    fixed = TRUE
  )
  table1$`Evidence from Other Omics Studies`[is.na(
    table1$`Evidence from Other Omics Studies`
  )] <- "(none found)"

  ## Split the relevant strings
  table1$`Evidence from Other Omics Studies` <- strsplit(
    table1$`Evidence from Other Omics Studies`,
    ", ",
    fixed = TRUE
  )

  # Clean up "Link to Monogenetic Disease" column
  table1$`Link to Monogenetic Disease` <- vapply(
    table1$`Link to Monogenetic Disease`,
    function(x) {
      omim_num_match <- stringr::str_extract_all(x, "\\b\\d{6}\\b")[[1L]]

      if (length(omim_num_match) == 0L) {
        NA_character_
      } else {
        toString(omim_num_match)
      }
    },
    character(1L),
    USE.NAMES = FALSE
  )

  table1$`Link to Monogenetic Disease` <- gsub(
    "NA",
    NA,
    table1$`Link to Monogenetic Disease`,
    fixed = TRUE
  )
  table1$`Link to Monogenetic Disease`[is.na(
    table1$`Link to Monogenetic Disease`
  )] <- "(none found)"

  # Split the relevant strings
  table1$`Link to Monogenetic Disease` <- strsplit(
    table1$`Link to Monogenetic Disease`,
    ", ",
    fixed = TRUE
  )

  # Clean up "References" column
  table1$References <- vapply(
    table1$References,
    function(x) {
      pmid_match <- stringr::str_extract_all(x, "\\b\\d{8}\\b")[[1L]]

      if (length(pmid_match) == 0L) {
        NA_character_
      } else {
        toString(pmid_match)
      }
    },
    character(1L),
    USE.NAMES = FALSE
  )

  table1$References <- gsub("NA", NA, table1$References, fixed = TRUE)
  table1$References[is.na(table1$References)] <- "(reference needed)"

  # Split the relevant strings
  table1$References <- strsplit(table1$References, ", ", fixed = TRUE)

  # Last cleanup steps
  table1$`Brain Cell Types` <- gsub(
    "ALL",
    "all",
    table1$`Brain Cell Types`,
    fixed = TRUE
  )
  table1$`Brain Cell Types`[is.na(table1$`Brain Cell Types`)] <- "(unknown)"
  table1$`Affected Pathway`[is.na(table1$`Affected Pathway`)] <- "(unknown)"
  table1$`Affected Pathway` <- tolower(table1$`Affected Pathway`)

  table1$`Mendelian Randomization` <- gsub(
    "Y",
    "Yes",
    table1$`Mendelian Randomization`,
    fixed = TRUE
  )
  table1$`Mendelian Randomization` <- gsub(
    "N",
    "No",
    table1$`Mendelian Randomization`,
    fixed = TRUE
  )

  table1$`GWAS Trait`[[39L]][2L] <- gsub(
    "small vessel stroke",
    "SVS",
    table1$`GWAS Trait`[[39L]][2L],
    fixed = TRUE
  )

  names(table1) <- gsub(
    "Link to Monogenetic Disease",
    "Link to Monogenic Disease",
    names(table1),
    fixed = TRUE
  )

  names(table1) <- gsub(
    "Evidence from Other Omics",
    "Evidence From Other Omics",
    names(table1),
    fixed = TRUE
  )

  table1
}
# nolint end: object_usage_linter.
