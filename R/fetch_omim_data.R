# fetch_omim_data.R
# OMIM data extraction and link generation functions

#' Extract Unique OMIM Numbers from Table1
#'
#' Extracts unique OMIM numbers from a table1 Link to Monogenic Disease column.
#'
#' @param monogenic_disease_col A list column of OMIM numbers.
#'
#' @return A character vector of unique OMIM numbers.
#'
#' @export
extract_unique_omim_nums <- function(monogenic_disease_col) {
  omim_nums <- unique(unlist(monogenic_disease_col))
  omim_nums <- omim_nums[!is.na(omim_nums) & omim_nums != "(none found)"]
  omim_nums
}

#' Generate OMIM Link
#'
#' Generates an OMIM entry URL for a single OMIM number.
#'
#' @param omim_num A character or numeric OMIM number.
#'
#' @return A character string with the OMIM URL.
#'
#' @export
generate_omim_link <- function(omim_num) {
  paste0(
    "https://www.omim.org/entry/",
    omim_num,
    "?search=",
    omim_num,
    "&highlight=",
    omim_num
  )
}

#' Generate OMIM Links for Multiple Numbers
#'
#' Creates a data.frame with OMIM numbers and their corresponding URLs.
#'
#' @param omim_nums A character vector of OMIM numbers.
#'
#' @return A data.frame with columns: omim_nums, omim_links.
#'
#' @export
generate_all_omim_links <- function(omim_nums) {
  omim_links <- vapply(
    omim_nums,
    generate_omim_link,
    character(1L),
    USE.NAMES = FALSE
  )

  data.frame(
    omim_nums = omim_nums,
    omim_links = omim_links,
    stringsAsFactors = FALSE
  )
}

#' Process OMIM Data from Table1
#'
#' Extracts OMIM numbers from table1 and generates links.
#' Optionally exports to CSV for manual data entry.
#'
#' @param monogenic_disease_col A list column of OMIM numbers from table1.
#' @param export_csv If TRUE, exports result to CSV. Defaults to FALSE.
#' @param csv_path Path for CSV export. Defaults to "omim_numbers.csv".
#'
#' @return A data.frame with columns: omim_nums, omim_links.
#'
#' @export
process_omim_data <- function(
  monogenic_disease_col,
  export_csv = FALSE,
  csv_path = "omim_numbers.csv"
) {
  # Extract unique OMIM numbers
  omim_nums <- extract_unique_omim_nums(monogenic_disease_col)

  # Generate links
  result <- generate_all_omim_links(omim_nums)

  # Optionally export to CSV for manual data entry

  # (OMIM API key approval may be pending)
  if (export_csv) {
    readr::write_csv(result, file = csv_path)
    message(sprintf("Exported OMIM numbers to: %s", csv_path))
  }

  result
}
