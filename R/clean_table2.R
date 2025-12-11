# clean_table2.R
# Table 2 data cleaning function

#' Clean Table 2 Data
#'
#' Reads and cleans the raw Table 2 CSV file, performing column
#' renaming, NA handling, and type conversions.
#'
#' @param file_path Path to the raw table2.csv file.
#'   Defaults to "table2.csv" in the current directory.
#'
#' @return A cleaned data.frame ready for display.
#'
#' @export
clean_table2 <- function(file_path = "table2.csv") {
  table2 <- readr::read_csv(file = file_path, show_col_types = FALSE)

  # Clean column names
  names(table2) <- gsub("_", " ", names(table2), fixed = TRUE)
  names(table2) <- tools::toTitleCase(names(table2))
  names(table2) <- gsub("Svd", "SVD", names(table2), fixed = TRUE)

  # Handle NA values

  table2$`Genetic Target`[is.na(table2$`Genetic Target`)] <- "(none)"

  # Convert types
  table2$`Target Sample Size` <- as.character(table2$`Target Sample Size`)

  table2
}
