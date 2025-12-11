# clean_table1.R
# Table 1 data cleaning function

#' Clean Table 1 Data
#'
#' Reads and cleans the raw Table 1 CSV file, performing various
#' data transformations including column renaming, NA handling,
#' and text cleanup.
#'
#' @param file_path Path to the raw table1.csv file.
#'   Defaults to "table1.csv" in the current directory.
#'
#' @return A cleaned data.frame with processed columns ready for display.
#'
#' @export
clean_table1 <- function(file_path = file.path(".", "table1.csv")) {
  # Load Table 1 data
  table1 <- read.csv(file_path, header = TRUE, sep = ",")

  # Replace empty cells with NA
  table1[!nzchar(table1, keepNA = TRUE)] <- NA
  table1[table1 == " "] <- NA

  # Replace NA in `Link to Monogenetic Disease` column by char N
  table1$mendelian_randomization[is.na(table1$mendelian_randomization)] <- "N"

  # Reorder and clean column names
  table1 <- table1 |>
    dplyr::select("gene", dplyr::everything())

  names(table1) <- gsub("_", " ", names(table1), fixed = TRUE)
  names(table1) <- tools::toTitleCase(names(table1))

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
