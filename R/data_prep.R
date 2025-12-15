# data_prep.R
# Data loading and preprocessing
# nolint start: object_usage_linter.

#' Safely load an RData file with error handling
#'
#' Wraps the base load() function with tryCatch to provide informative
#' error messages when data files are missing or corrupt.
#'
#' @param file_path Character. Path to the RData file.
#' @param env Environment. Environment to load into (default: parent frame).
#'
#' @return Invisible NULL. Side effect: objects loaded into env.
#'
#' @keywords internal
safe_load_rdata <- function(file_path, envir = parent.frame()) {
  tryCatch(
    {
      load(file_path, envir = envir)
    },
    error = function(e) {
      stop(
        sprintf(
          "Failed to load data file '%s': %s\n%s",
          file_path,
          e$message,
          "Please ensure the file exists and is not corrupt."
        ),
        call. = FALSE
      )
    }
  )
}

#' Safely read a CSV file with error handling
#'
#' Wraps data.table::fread with tryCatch to provide informative
#' error messages when CSV files are missing or malformed.
#'
#' @param file_path Character. Path to the CSV file.
#'
#' @return A data.table containing the CSV data.
#'
#' @keywords internal
safe_read_csv <- function(file_path) {
  tryCatch(
    {
      data.table::fread(file_path)
    },
    error = function(e) {
      stop(
        sprintf(
          "Failed to read CSV file '%s': %s\n%s",
          file_path,
          e$message,
          "Please ensure the file exists and is properly formatted."
        ),
        call. = FALSE
      )
    }
  )
}

#' Load and prepare Table 1 data
#'
#' Loads pre-cleaned RData files and external data sources, applies formatting
#' corrections, and creates pre-computed lookup tables for efficient filtering
#' in the Shiny application.
#'
#' @details
#' This function performs the following operations:
#' - Loads table1_clean.RData, gene_info_results_df.RData, prot_info_clean.RData
#' - Fixes empty Mendelian Randomization values
#' - Applies title case formatting to pathway and protein names
#' - Extracts unique GWAS traits and omics types
#' - Pre-computes row indices for GWAS and omics filtering
#' - Loads OMIM info and publication references
#'
#' @return A named list containing:
#' \describe{
#'   \item{table1}{Main gene data as a data.table}
#'   \item{gene_info_results_df}{NCBI gene info with ID, protein, aliases, URL}
#'   \item{prot_info_clean}{UniProt protein information}
#'   \item{omics_df}{Data frame of omics types and full names}
#'   \item{unique_gwas_traits}{Character vector of unique GWAS traits}
#'   \item{gwas_traits_df}{Data frame of GWAS traits}
#'   \item{gwas_trait_mapping}{Named vector mapping trait abbreviations
#'     to full names}
#'   \item{gwas_trait_rows}{List of row indices per GWAS trait
#'     for fast filtering}
#'   \item{omics_type_rows}{List of row indices per omics type
#'     for fast filtering}
#'   \item{omim_info}{OMIM phenotype information}
#'   \item{refs}{Publication references for tooltips}
#' }
#'
#' @export
#' @seealso \code{\link{load_table2_data}} for clinical trial data
load_and_prepare_data <- function() {
  # Load required data with error handling
  message("Loading Table 1 data...")
  safe_load_rdata(DATA_PATHS$table1_clean, envir = environment())

  # Fix empty Mendelian Randomization values (should be "No")
  table1$`Mendelian Randomization`[
    table1$`Mendelian Randomization` == "" |
      is.na(table1$`Mendelian Randomization`)
  ] <- "No"

  # Load pre-fetched NCBI gene info
  message("Loading gene info...")
  safe_load_rdata(DATA_PATHS$gene_info, envir = environment())
  gene_info_results_df <- dplyr::rename(
    gene_info_results_df,
    `NCBI Gene ID` = "uid",
    `Protein Coded by This Gene` = "description",
    `Other Aliases` = "otheraliases"
  )
  gene_info_results_df$`Protein Coded by This Gene` <- tools::toTitleCase(
    gene_info_results_df$`Protein Coded by This Gene`
  )

  # Add URL to NCBI Gene webpage using constant

  gene_info_results_df$URL <- paste0(
    NCBI_GENE_BASE_URL,
    gene_info_results_df$`NCBI Gene ID`
  )

  # Load pre-fetched protein info
  message("Loading protein info...")
  safe_load_rdata(DATA_PATHS$prot_info, envir = environment())
  prot_info_clean <- prot_info_clean

  # Title casing for the Affected Pathway column
  table1$`Affected Pathway` <- tools::toTitleCase(table1$`Affected Pathway`)

  # Preserve WNT capitalization after title case conversion
  table1$`Affected Pathway` <- gsub(
    "Wnt",
    "WNT",
    table1$`Affected Pathway`,
    fixed = TRUE
  )

  # Standardize proteomics capitalization - must handle list column properly
  table1$`Evidence From Other Omics Studies` <- lapply(
    table1$`Evidence From Other Omics Studies`,
    function(x) gsub("proteomics", "Proteomics", x, fixed = TRUE)
  )

  # Vectorized extraction of omics types
  all_omics_types <- table1$`Evidence From Other Omics Studies` |>
    purrr::map(function(omics_evidence) {
      if (
        length(omics_evidence) > 0L &&
          omics_evidence[1L] != PLACEHOLDER_NONE_FOUND
      ) {
        vapply(omics_evidence, function(x) sub(";.*", "", x), character(1L))
      } else {
        character(0L)
      }
    }) |>
    unlist() |>
    (\(x) x[grepl("^[A-Z]{4}$", x)])()

  omics_df <- data.frame(
    Omics_Type = unique(all_omics_types),
    stringsAsFactors = FALSE
  )

  # Use constant for omics full names
  omics_df$Full_Name <- OMICS_FULL_NAMES[omics_df$Omics_Type]

  # Vectorized extraction of GWAS traits
  gwas_traits_all <- table1$`GWAS Trait` |>
    purrr::map(function(gwas_traits) {
      if (is.list(gwas_traits)) {
        gwas_traits <- gwas_traits[[1L]]
      }
      if (length(gwas_traits) > 0L && !is.na(gwas_traits[1L])) {
        gwas_traits
      } else {
        character(0L)
      }
    }) |>
    unlist()

  unique_gwas_traits <- unique(gwas_traits_all)
  unique_gwas_traits <- unique_gwas_traits[
    unique_gwas_traits != PLACEHOLDER_NONE_FOUND
  ]

  gwas_traits_df <- data.frame(
    GWAS_Trait = unique_gwas_traits,
    stringsAsFactors = FALSE
  )

  # Load GWAS trait full names for tooltips
  message("Loading GWAS trait mappings...")
  safe_load_rdata(DATA_PATHS$gwas_trait_names, envir = environment())

  first_mapping <- data.frame(
    abbrev = names(gwas_trait_names)[1L],
    full_name = names(gwas_trait_names)[2L],
    stringsAsFactors = FALSE
  )

  colnames(gwas_trait_names) <- c("abbrev", "full_name")
  gwas_trait_names <- rbind(first_mapping, gwas_trait_names)

  gwas_trait_mapping <- setNames(
    gwas_trait_names$full_name,
    gwas_trait_names$abbrev
  )

  # Pre-compute GWAS trait membership for fast filtering (using fastmap)
  gwas_trait_rows <- fastmap::fastmap()
  for (trait in unique_gwas_traits) {
    gwas_trait_rows$set(
      trait,
      which(purrr::map_lgl(
        table1$`GWAS Trait`,
        ~ any(.x %in% trait)
      ))
    )
  }

  # Add "(none found)" rows for filtering
  gwas_trait_rows$set(
    PLACEHOLDER_NONE_FOUND,
    which(purrr::map_lgl(
      table1$`GWAS Trait`,
      function(traits) {
        if (is.list(traits)) {
          traits <- traits[[1L]]
        }
        length(traits) == 0L ||
          is.null(traits) ||
          (length(traits) == 1L &&
            (is.na(traits[1L]) || traits[1L] == PLACEHOLDER_NONE_FOUND))
      }
    ))
  )

  # Pre-compute omics type membership for fast filtering (using fastmap)
  omics_type_rows <- fastmap::fastmap()
  for (omics_type in omics_df$Omics_Type) {
    omics_type_rows$set(
      omics_type,
      which(purrr::map_lgl(
        table1$`Evidence From Other Omics Studies`,
        function(omics_evidence) {
          if (
            length(omics_evidence) > 0L &&
              omics_evidence[1L] != PLACEHOLDER_NONE_FOUND
          ) {
            omics_types <- vapply(
              omics_evidence,
              function(x) sub(";.*", "", x),
              character(1L)
            )
            any(omics_types == omics_type)
          } else {
            FALSE
          }
        }
      ))
    )
  }

  # Add "(none found)" rows for omics filtering
  omics_type_rows$set(
    PLACEHOLDER_NONE_FOUND,
    which(purrr::map_lgl(
      table1$`Evidence From Other Omics Studies`,
      function(omics_evidence) {
        if (is.list(omics_evidence)) {
          omics_evidence <- omics_evidence[[1L]]
        }
        length(omics_evidence) == 0L ||
          is.null(omics_evidence) ||
          (length(omics_evidence) == 1L &&
            (is.na(omics_evidence[1L]) ||
              omics_evidence[1L] == PLACEHOLDER_NONE_FOUND))
      }
    ))
  )

  # Load OMIM info (using safe CSV reader)
  message("Loading OMIM info...")
  omim_info <- safe_read_csv(DATA_PATHS$omim_info)

  # Pre-process UTF-8 conversions for OMIM text fields (avoids per-row iconv)
  omim_info$phenotype_clean <- iconv(
    as.character(omim_info$phenotype),
    to = "UTF-8",
    sub = ""
  )
  omim_info$inheritance_clean <- iconv(
    as.character(omim_info$inheritance),
    to = "UTF-8",
    sub = ""
  )
  omim_info$gene_or_locus_clean <- iconv(
    as.character(omim_info$gene_or_locus),
    to = "UTF-8",
    sub = ""
  )
  omim_info$gene_or_locus_mim_number_clean <- iconv(
    as.character(omim_info$gene_or_locus_mim_number),
    to = "UTF-8",
    sub = ""
  )

  # Set data.table key for fast OMIM lookups by omim_num
  data.table::setDT(omim_info)
  data.table::setkey(omim_info, omim_num)

  # Create fastmap for O(1) OMIM lookups (pre-compute tooltip content)
  message("Pre-computing OMIM lookup map...")
  omim_lookup <- fastmap::fastmap()
  for (i in seq_len(nrow(omim_info))) {
    omim_num <- as.character(omim_info$omim_num[i])
    omim_lookup$set(
      omim_num,
      list(
        phenotype = omim_info$phenotype_clean[i],
        inheritance = omim_info$inheritance_clean[i],
        gene_or_locus = omim_info$gene_or_locus_clean[i],
        gene_or_locus_mim_number = omim_info$gene_or_locus_mim_number_clean[i],
        omim_link = as.character(omim_info$omim_link[i])
      )
    )
  }

  # Load pre-fetched references data
  message("Loading publication references...")
  safe_load_rdata(DATA_PATHS$refs, envir = environment())
  refs <- refs

  # Convert table1 to data.table for faster filtering
  data.table::setDT(table1)

  # Pre-assign row_id column to avoid recomputing on each filter operation

  table1[, row_id := .I]

  message("Table 1 data preparation complete.")

  list(
    table1 = table1,
    gene_info_results_df = gene_info_results_df,
    prot_info_clean = prot_info_clean,
    omics_df = omics_df,
    unique_gwas_traits = unique_gwas_traits,
    gwas_traits_df = gwas_traits_df,
    gwas_trait_mapping = gwas_trait_mapping,
    gwas_trait_rows = gwas_trait_rows,
    omics_type_rows = omics_type_rows,
    omim_info = omim_info,
    omim_lookup = omim_lookup,
    refs = refs
  )
}

#' Load and prepare Table 2 clinical trial data
#'
#' Lazily loads clinical trial data and associated gene information.
#' Processes registry IDs, formats completion dates, and prepares
#' trial metadata for display.
#'
#' @details
#' This function performs the following operations:
#' - Loads table2_clean.RData and gene_info_table2.RData
#' - Renames gene info columns to match Table 1 format
#' - Extracts trial name and primary outcome into ct_info
#' - Converts completion dates to "Month Year" format
#' - Identifies clinical trial registry types (NCT, ISRCTN, ACTRN, ChiCTR)
#'
#' @return A named list containing:
#' \describe{
#'   \item{table2}{Clinical trial data as a data.table}
#'   \item{ct_info}{Trial name and primary outcome for tooltips}
#'   \item{registry_matches}{Data frame of matched registry patterns}
#'   \item{registry_rows}{Fastmap of registry pattern to row indices}
#'   \item{gene_info_table2}{NCBI gene info for Table 2 genes}
#'   \item{gene_symbols_table2}{Character vector of gene symbols}
#'   \item{sample_sizes}{Numeric vector of sample sizes for histogram}
#'   \item{sample_sizes_hash}{Pre-computed digest hash for caching}
#' }
#'
#' @export
#' @seealso \code{\link{load_and_prepare_data}} for main gene data
load_table2_data <- function() {
  message("Loading Table 2 clinical trial data...")
  safe_load_rdata(DATA_PATHS$table2_clean, envir = environment())

  message("Loading Table 2 gene info...")
  safe_load_rdata(DATA_PATHS$gene_info_table2, envir = environment())

  # Rename columns and add URL (same as Table 1 gene info)
  gene_info_table2 <- dplyr::rename(
    gene_info_table2,
    `NCBI Gene ID` = "uid",
    `Protein Coded by This Gene` = "description",
    `Other Aliases` = "otheraliases"
  )
  gene_info_table2$`Protein Coded by This Gene` <- tools::toTitleCase(
    gene_info_table2$`Protein Coded by This Gene`
  )
  gene_info_table2$URL <- paste0(
    NCBI_GENE_BASE_URL,
    gene_info_table2$`NCBI Gene ID`
  )

  # Extract trial name and primary outcome using column names
  ct_info <- data.frame(
    ct_info = I(Map(
      function(a, b) list(c(a, b)),
      table2$`Trial Name`,
      table2$`Primary Outcome`
    ))
  )

  table2$`Trial Name` <- NULL
  table2$`Primary Outcome` <- NULL

  convert_to_date <- function(date_column) {
    is_month_year <- grepl("^\\d{1,2}/\\d{4}$", date_column)
    date_column[is_month_year] <- paste0(date_column[is_month_year], "-01")
    date_column[!is_month_year] <- NA
    date_vals <- as.Date(date_column, format = "%m/%Y-%d")
    format(date_vals, "%B %Y")
  }

  table2$`Estimated Completion Date` <- convert_to_date(
    table2$`Estimated Completion Date`
  )

  # Mark unpublished completed trials using constant
  # Note: Row 14 contains a trial that is completed but unpublished
  # This is a known data quirk that should be handled in data cleaning
  unpublished_idx <- which(
    is.na(table2$`Estimated Completion Date`) &
      !is.na(table2$Drug)
  )
  if (length(unpublished_idx) > 0L && 14L %in% seq_len(nrow(table2))) {
    table2$`Estimated Completion Date`[14L] <- COMPLETION_UNPUBLISHED
  }

  # Use constant for registry patterns
  registry_matches <- data.frame(
    matched_pattern = purrr::map_chr(
      table2$`Registry ID`,
      function(registry_id) {
        matches <- vapply(
          REGISTRY_PATTERNS,
          grepl,
          logical(1L),
          x = registry_id,
          ignore.case = TRUE
        )
        if (any(matches)) {
          REGISTRY_PATTERNS[matches][1L]
        } else {
          NA_character_
        }
      }
    ),
    stringsAsFactors = FALSE
  )

  # Pre-compute registry pattern to row indices mapping (fastmap O(1) lookup)
  registry_rows <- fastmap::fastmap()
  for (pattern in REGISTRY_PATTERNS) {
    registry_rows$set(
      pattern,
      which(registry_matches$matched_pattern == pattern)
    )
  }

  data.table::setDT(table2)

  # Pre-compute sample_size_numeric column (avoids re-computing on each filter)
  table2[,
    sample_size_numeric := as.numeric(gsub(
      "[^0-9]",
      "",
      `Target Sample Size`
    ))
  ]

  # Set data.table keys for faster filtering
  data.table::setindex(table2, `Genetic Evidence`)
  data.table::setindex(table2, `Clinical Trial Phase`)
  data.table::setindex(table2, `SVD Population`)

  # Pre-assign original_row_num column to avoid recomputing on each filter
  table2[, original_row_num := .I]

  # Extract gene symbols from gene_info_table2 (stored in "name" column)
  gene_symbols_table2 <- gene_info_table2$name

  # Pre-compute sample sizes for histogram (avoids re-computing on each draw)
  sample_sizes <- table2$sample_size_numeric

  # Pre-compute digest hash for histogram caching
  # (avoids recomputing digest on each render)
  sample_sizes_hash <- digest::digest(sample_sizes)

  message("Table 2 data preparation complete.")

  list(
    table2 = table2,
    ct_info = ct_info,
    registry_matches = registry_matches,
    registry_rows = registry_rows,
    gene_info_table2 = gene_info_table2,
    gene_symbols_table2 = gene_symbols_table2,
    sample_sizes = sample_sizes,
    sample_sizes_hash = sample_sizes_hash
  )
}

#' Aggregate Table 2 display data by Drug
#'
#' Groups clinical trial data by Drug, Mechanism of Action, and Genetic
#' Target, collapsing other columns (multiple trials per drug) into single
#' cells with HTML line breaks.
#'
#' @param table2_display Data frame. The display-ready table2 with HTML
#'   tooltips.
#'
#' @return Data frame. Aggregated table with merged drug rows.
#'
#' @details
#' Columns are aggregated as follows:
#' - Drug, Mechanism of Action, Genetic Target: kept as grouping keys
#' - Other columns: unique values collapsed with <br> separators
#'
#' @export
#' @seealso \code{\link{prepare_table2_display}}
aggregate_table2_by_drug <- function(table2_display) {
  # Convert to data.frame if data.table

  df <- as.data.frame(table2_display)

  # Define grouping columns (these repeat for same drug)
  group_cols <- c("Drug", "Mechanism of Action", "Genetic Target")

  # Define columns to aggregate (collapse with <br>)
  agg_cols <- setdiff(names(df), group_cols)

  # Helper function to collapse values

  collapse_unique <- function(x) {
    # Remove duplicates and NA, then collapse with <br>
    unique_vals <- unique(x[!is.na(x) & x != ""])
    if (length(unique_vals) == 0L) {
      return(NA_character_)
    }
    paste(unique_vals, collapse = "<br>")
  }

  # Group and aggregate
  aggregated <- df |>
    dplyr::group_by(
      dplyr::across(dplyr::all_of(group_cols))
    ) |>
    dplyr::summarise(
      dplyr::across(
        dplyr::all_of(agg_cols),
        collapse_unique
      ),
      .groups = "drop"
    )

  # Reorder columns to match original order
  aggregated <- aggregated[, names(df)]

  as.data.frame(aggregated)
}
# nolint end: object_usage_linter.
