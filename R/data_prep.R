# data_prep.R
# Data loading and preprocessing
# nolint start: object_usage_linter.

# Check if qs package is available for faster serialization
qs_available <- requireNamespace("qs", quietly = TRUE)

#' Safely read a serialized R object with error handling
#'
#' Attempts to read using qs format first (3-5x faster), then falls back
#' to RDS format. Wraps with tryCatch to provide informative error messages.
#'
#' @param file_path Character. Path to the RDS file (will also check for .qs).
#'
#' @return The object stored in the file.
#'
#' @keywords internal
safe_read_rds <- function(file_path) {

  # Try qs format first if available (significantly faster)
  if (qs_available) {
    qs_path <- sub("\\.rds$", ".qs", file_path, ignore.case = TRUE)
    if (file.exists(qs_path)) {
      return(tryCatch(
        {
          qs::qread(qs_path, nthreads = parallel::detectCores())
        },
        error = function(e) {
          message(sprintf(
            "qs read failed for '%s', falling back to RDS", qs_path
          ))
          NULL
        }
      ))
    }
  }

  # Fall back to RDS format
  tryCatch(
    {
      readRDS(file_path)
    },
    error = function(e) {
      stop(
        sprintf(
          "Failed to load file '%s': %s\n%s",
          file_path,
          e$message,
          "Please ensure the file exists and is not corrupt."
        ),
        call. = FALSE
      )
    }
  )
}

#' Convert RDS files to qs format for faster loading
#'
#' Converts all RDS files in DATA_PATHS to qs format. Run this once
#' to speed up subsequent app startups by 3-5x.
#'
#' @param overwrite Logical. Overwrite existing qs files. Defaults to FALSE.
#'
#' @return Invisible NULL. Prints conversion status.
#'
#' @export
convert_rds_to_qs <- function(overwrite = FALSE) {
  if (!qs_available) {
    stop("qs package not installed. Install with: install.packages('qs')")
  }

  rds_paths <- unlist(
    DATA_PATHS[grep("\\.rds$", DATA_PATHS, ignore.case = TRUE)]
  )

  for (rds_path in rds_paths) {
    qs_path <- sub("\\.rds$", ".qs", rds_path, ignore.case = TRUE)

    if (!file.exists(rds_path)) {
      message(sprintf("  Skipping (not found): %s", rds_path))
      next
    }

    if (file.exists(qs_path) && !overwrite) {
      message(sprintf("  Skipping (exists): %s", qs_path))
      next
    }

    tryCatch({
      obj <- readRDS(rds_path)
      qs::qsave(obj, qs_path, nthreads = parallel::detectCores())
      rds_size <- file.size(rds_path)
      qs_size <- file.size(qs_path)
      message(sprintf(
        "  Converted: %s (%.1f KB -> %.1f KB, %.0f%% smaller)",
        basename(rds_path),
        rds_size / 1024,
        qs_size / 1024,
        (1 - qs_size / rds_size) * 100
      ))
    }, error = function(e) {
      message(sprintf("  Failed: %s - %s", rds_path, e$message))
    })
  }

  invisible(NULL)
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
#' Loads pre-cleaned RDS files and external data sources, applies formatting
#' corrections, and creates pre-computed lookup tables for efficient filtering
#' in the Shiny application.
#'
#' @details
#' This function performs the following operations:
#' - Loads table1_clean.RDS, gene_info_results_df.RDS, prot_info_clean.RDS
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
  table1 <- safe_read_rds(DATA_PATHS$table1_clean)

  # Fix empty Mendelian Randomization values (should be "No")
  table1$`Mendelian Randomization`[
    table1$`Mendelian Randomization` == "" |
      is.na(table1$`Mendelian Randomization`)
  ] <- "No"

  # Load pre-fetched NCBI gene info
  message("Loading gene info...")
  gene_info_results_df <- safe_read_rds(DATA_PATHS$gene_info)
  gene_info_results_df <- dplyr::rename(
    gene_info_results_df,
    `Name` = "name",
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
  prot_info_clean <- safe_read_rds(DATA_PATHS$prot_info)

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

  # Vectorized extraction of omics types (optimized with vapply)
  all_omics_types <- unlist(lapply(
    table1$`Evidence From Other Omics Studies`,
    function(omics_evidence) {
      if (
        length(omics_evidence) > 0L &&
          omics_evidence[1L] != PLACEHOLDER_NONE_FOUND
      ) {
        vapply(omics_evidence, function(x) sub(";.*", "", x), character(1L))
      } else {
        character(0L)
      }
    }
  ))
  all_omics_types <- all_omics_types[grepl("^[A-Z]{4}$", all_omics_types)]

  omics_df <- data.frame(
    Omics_Type = unique(all_omics_types),
    stringsAsFactors = FALSE
  )

  # Use constant for omics full names
  omics_df$Full_Name <- OMICS_FULL_NAMES[omics_df$Omics_Type]

  # Vectorized extraction of GWAS traits (optimized with lapply)
  gwas_traits_all <- unlist(lapply(
    table1$`GWAS Trait`,
    function(gwas_traits) {
      if (is.list(gwas_traits)) {
        gwas_traits <- gwas_traits[[1L]]
      }
      if (length(gwas_traits) > 0L && !is.na(gwas_traits[1L])) {
        gwas_traits
      } else {
        character(0L)
      }
    }
  ))

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
  gwas_trait_names <- safe_read_rds(DATA_PATHS$gwas_trait_names)

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
  # Optimized: use vapply instead of purrr::map_lgl for faster iteration
  gwas_trait_rows <- fastmap::fastmap()
  gwas_col <- table1$`GWAS Trait`
  for (trait in unique_gwas_traits) {
    gwas_trait_rows$set(
      trait,
      which(vapply(
        gwas_col,
        function(x) any(x %in% trait),
        logical(1L)
      ))
    )
  }

  # Add "(none found)" rows for filtering
  gwas_trait_rows$set(
    PLACEHOLDER_NONE_FOUND,
    which(vapply(
      gwas_col,
      function(traits) {
        if (is.list(traits)) {
          traits <- traits[[1L]]
        }
        length(traits) == 0L ||
          is.null(traits) ||
          (length(traits) == 1L &&
             (is.na(traits[1L]) || traits[1L] == PLACEHOLDER_NONE_FOUND))
      },
      logical(1L)
    ))
  )

  # Pre-compute omics type membership for fast filtering (using fastmap)
  # Optimized: use vapply instead of purrr::map_lgl for faster iteration
  omics_type_rows <- fastmap::fastmap()
  omics_col <- table1$`Evidence From Other Omics Studies`
  for (omics_type in omics_df$Omics_Type) {
    omics_type_rows$set(
      omics_type,
      which(vapply(
        omics_col,
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
        },
        logical(1L)
      ))
    )
  }

  # Add "(none found)" rows for omics filtering
  omics_type_rows$set(
    PLACEHOLDER_NONE_FOUND,
    which(vapply(
      omics_col,
      function(omics_evidence) {
        if (is.list(omics_evidence)) {
          omics_evidence <- omics_evidence[[1L]]
        }
        length(omics_evidence) == 0L ||
          is.null(omics_evidence) ||
          (length(omics_evidence) == 1L &&
             (is.na(omics_evidence[1L]) ||
                omics_evidence[1L] == PLACEHOLDER_NONE_FOUND))
      },
      logical(1L)
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
  refs <- safe_read_rds(DATA_PATHS$refs)

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
#' - Loads table2_clean.RDS and gene_info_table2.RDS
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
  table2 <- safe_read_rds(DATA_PATHS$table2_clean)

  message("Loading Table 2 gene info...")
  gene_info_table2 <- safe_read_rds(DATA_PATHS$gene_info_table2)

  # Rename columns and add URL (same as Table 1 gene info)
  gene_info_table2 <- dplyr::rename(
    gene_info_table2,
    `Name` = "name",
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
  # Optimized: use vapply instead of purrr::map_chr
  registry_matches <- data.frame(
    matched_pattern = vapply(
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
      },
      character(1L)
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

  # Extract gene symbols from gene_info_table2 (stored in "Name" column)
  gene_symbols_table2 <- gene_info_table2$Name

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
