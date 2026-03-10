# data_prep.R
# Data loading and preprocessing
# nolint start: object_usage_linter.

.N_THREADS <- parallel::detectCores()

# =============================================================================
# DATA FILE VERIFICATION
# =============================================================================

# Verify Required Data Files Exist
#
# Checks that all required data files exist and are non-empty.
# Logs file sizes for debugging purposes.
#
# Args:
#   data_paths: Named list. File paths to verify (defaults to DATA_PATHS).
#
# Returns:
#   Invisible TRUE if all files verified, stops with error otherwise.
verify_data_files <- function(data_paths = DATA_PATHS) {

  message("Verifying required data files...")

  all_files <- unlist(data_paths)
  missing_files <- character(0)
  empty_files <- character(0)


  for (name in names(all_files)) {
    file_path <- all_files[[name]]

    if (!file.exists(file_path)) {
      # Check for fallback (.qs -> .rds)
      if (grepl("\\.qs$", file_path)) {
        rds_path <- sub("\\.qs$", ".rds", file_path)
        if (file.exists(rds_path)) {
          message(sprintf("  %s: using RDS fallback", name))
          next
        }
      }
      missing_files <- c(missing_files, file_path)
      message(sprintf("  MISSING: %s", file_path))
      next
    }

    file_info <- file.info(file_path)
    if (is.na(file_info$size) || file_info$size == 0) {
      empty_files <- c(empty_files, file_path)
      message(sprintf("  EMPTY: %s", file_path))
      next
    }

    message(sprintf(
      "  OK: %s (%.1f KB)", basename(file_path), file_info$size / 1024
    ))
  }

  if (length(missing_files) > 0 || length(empty_files) > 0) {
    error_msg <- character(0)
    if (length(missing_files) > 0) {
      error_msg <- c(
        error_msg,
        sprintf("Missing files: %s", paste(missing_files, collapse = ", "))
      )
    }
    if (length(empty_files) > 0) {
      error_msg <- c(
        error_msg,
        sprintf("Empty files: %s", paste(empty_files, collapse = ", "))
      )
    }
    stop(paste(error_msg, collapse = "\n"), call. = FALSE)
  }

  message("All data files verified successfully.")
  invisible(TRUE)
}

# =============================================================================
# TESTABLE HELPER FUNCTIONS
# =============================================================================

# Convert Month/Year Date String to Display Format
#
# Converts date strings in "M/YYYY" format to "Month Year" display format.
# For example, "1/2024" becomes "January 2024".
#
# Args:
#   date_string: Character vector. Date strings in "M/YYYY" or "MM/YYYY" format.
#
# Returns:
#   Character vector with dates formatted as "Month Year", or NA for invalid.
convert_month_year_date <- function(date_string) {
  is_month_year <- grepl("^\\d{1,2}/\\d{4}$", date_string)
  result <- rep(NA_character_, length(date_string))
  if (any(is_month_year)) {
    valid_dates <- date_string[is_month_year]
    valid_dates <- paste0(valid_dates, "-01")
    date_vals <- as.Date(valid_dates, format = "%m/%Y-%d")
    result[is_month_year] <- format(date_vals, "%B %Y")
  }
  result
}

# Match Registry ID Against Known Patterns
#
# Matches a clinical trial registry ID against known registry patterns
# (NCT, ISRCTN, ACTRN, ChiCTR).
#
# Args:
#   registry_id: Character. A single registry ID to match.
#   patterns: Character vector. Patterns to match against.
#     Defaults to REGISTRY_PATTERNS constant.
#
# Returns:
#   Character. The matched pattern, or NA if no match found.
match_registry_pattern <- function(registry_id, patterns = REGISTRY_PATTERNS) {
  matches <- vapply(
    patterns,
    grepl,
    logical(1L),
    x = registry_id,
    ignore.case = TRUE
  )
  if (any(matches)) {
    patterns[matches][1L]
  } else {
    NA_character_
  }
}

# Extract Omics Type from Semicolon-Separated String
#
# Extracts the 4-letter omics code from a semicolon-separated string.
# For example, "PWAS; some detail" returns "PWAS".
#
# Args:
#   omics_string: Character. An omics evidence string.
#
# Returns:
#   Character. The extracted omics type code, or the original string if
#   no semicolon is present, or NA for empty/NA input.
extract_omics_type <- function(omics_string) {
  if (is.na(omics_string) || omics_string == "") {
    return(NA_character_)
  }

  sub(";.*", "", omics_string)
}

# Preserve WNT Capitalization After Title Case
#
# Applies title case conversion while preserving "WNT" capitalization.
# The "Wnt" pathway should always be displayed as "WNT".
#
# Args:
#   pathway_string: Character vector. Pathway names to format.
#
# Returns:
#   Character vector with title case applied and "WNT" preserved.
preserve_wnt_capitalization <- function(pathway_string) {
  # Apply title case first

  result <- tools::toTitleCase(pathway_string)
  # Restore WNT capitalization

  gsub("Wnt", "WNT", result, fixed = TRUE)
}

# Safely read a serialized R object with error handling
#
# Reads data files using qs format (default, 3-5x faster) with automatic
# fallback to RDS format for backward compatibility.
#
# Args:
#   file_path: Character. Path to the data file (.qs or .rds).
#
# Returns:
#   The object stored in the file.
safe_read_data <- function(file_path) {
  # If path is .qs, try it directly
  if (grepl("\\.qs$", file_path, ignore.case = TRUE)) {
    if (file.exists(file_path)) {
      return(tryCatch(
        qs::qread(file_path, nthreads = .N_THREADS),
        error = function(e) {
          stop(
            sprintf("Failed to load qs file '%s': %s", file_path, e$message),
            call. = FALSE
          )
        }
      ))
    }
    # Fall back to .rds if .qs doesn't exist
    rds_path <- sub("\\.qs$", ".rds", file_path, ignore.case = TRUE)
    if (file.exists(rds_path)) {
      message(sprintf("QS file not found, falling back to RDS: %s", rds_path))
      return(readRDS(rds_path))
    }
    stop(
      sprintf("Neither '%s' nor '%s' found", file_path, rds_path),
      call. = FALSE
    )
  }

  # If path is .rds (legacy), check for .qs version first
  if (grepl("\\.rds$", file_path, ignore.case = TRUE)) {
    qs_path <- sub("\\.rds$", ".qs", file_path, ignore.case = TRUE)
    if (file.exists(qs_path)) {
      return(qs::qread(qs_path, nthreads = .N_THREADS))
    }
    # Fall back to .rds
    if (file.exists(file_path)) {
      return(readRDS(file_path))
    }
  }

  stop(sprintf("File not found: %s", file_path), call. = FALSE)
}

# Alias for backward compatibility with existing code
safe_read_rds <- safe_read_data

# Convert RDS files to qs format for faster loading
#
# Converts all RDS files to qs format based on DATA_PATHS configuration.
# Run this once after deployment or when data files are updated.
#
# Args:
#   overwrite: Logical. Overwrite existing qs files. Defaults to FALSE.
#
# Returns:
#   Invisible NULL. Prints conversion status.
convert_rds_to_qs <- function(overwrite = FALSE) {
  message("Converting RDS files to QS format...")

  # Get qs paths from DATA_PATHS and derive rds paths
  qs_paths <- unlist(
    DATA_PATHS[grep("\\.qs$", DATA_PATHS, ignore.case = TRUE)]
  )

  for (qs_path in qs_paths) {
    rds_path <- sub("\\.qs$", ".rds", qs_path, ignore.case = TRUE)

    if (!file.exists(rds_path)) {
      message(sprintf("  Skipping (RDS not found): %s", rds_path))
      next
    }

    if (file.exists(qs_path) && !overwrite) {
      message(sprintf("  Skipping (QS exists): %s", basename(qs_path)))
      next
    }

    tryCatch({
      obj <- readRDS(rds_path)
      qs::qsave(obj, qs_path, nthreads = .N_THREADS)
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

  message("Conversion complete.")
  invisible(NULL)
}

# Safely read a CSV file with error handling
#
# Wraps data.table::fread with tryCatch to provide informative
# error messages when CSV files are missing or malformed.
#
# Args:
#   file_path: Character. Path to the CSV file.
#
# Returns:
#   A data.table containing the CSV data.
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

# Prepare gene info data frame with standard column names
#
# Renames columns from NCBI raw names to display names, applies title case
# to protein descriptions, and adds NCBI Gene URL.
#
# Args:
#   gene_info_df: Data frame. Raw NCBI gene info with columns: name, uid,
#     description, otheraliases.
#
# Returns:
#   Data frame with renamed columns and URL.
prepare_gene_info_df <- function(gene_info_df) {
  gene_info_df <- dplyr::rename(
    gene_info_df,
    `Name` = "name",
    `NCBI Gene ID` = "uid",
    `Protein Coded by This Gene` = "description",
    `Other Aliases` = "otheraliases"
  )
  gene_info_df$`Protein Coded by This Gene` <- tools::toTitleCase(
    gene_info_df$`Protein Coded by This Gene`
  )
  gene_info_df$URL <- paste0(
    NCBI_GENE_BASE_URL,
    gene_info_df$`NCBI Gene ID`
  )
  gene_info_df
}

# Load and prepare Table 1 data
#
# Loads pre-cleaned RDS files and external data sources, applies formatting
# corrections, and creates pre-computed lookup tables for efficient filtering
# in the Shiny application.
#
# Details:
#   This function performs the following operations:
#   - Loads table1_clean.RDS, gene_info_results_df.RDS, prot_info_clean.RDS
#   - Fixes empty Mendelian Randomization values
#   - Applies title case formatting to pathway and protein names
#   - Extracts unique GWAS traits and omics types
#   - Pre-computes row indices for GWAS and omics filtering
#   - Loads OMIM info and publication references
#
# Returns:
#   A named list containing:
#   - table1: Main gene data as a data.table
#   - gene_info_results_df: NCBI gene info with ID, protein, aliases, URL
#   - prot_info_clean: UniProt protein information
#   - omics_df: Data frame of omics types and full names
#   - unique_gwas_traits: Character vector of unique GWAS traits
#   - gwas_traits_df: Data frame of GWAS traits
#   - gwas_trait_mapping: Named vector mapping trait abbreviations to full names
#   - gwas_trait_rows: List of row indices per GWAS trait for fast filtering
#   - omics_type_rows: List of row indices per omics type for fast filtering
#   - omim_lookup: fastmap for O(1) OMIM lookups by omim_num
#   - refs: Publication references for tooltips
load_and_prepare_data <- function() {
  # Verify all data files exist before attempting to load
  verify_data_files()

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
  gene_info_results_df <- prepare_gene_info_df(gene_info_results_df)

  # Load pre-fetched protein info
  message("Loading protein info...")
  prot_info_clean <- safe_read_rds(DATA_PATHS$prot_info)

  # Create fastmap for O(1) protein lookups by gene name
  message("Pre-computing protein info lookup map...")
  prot_info_lookup <- fastmap::fastmap()
  for (i in seq_len(nrow(prot_info_clean))) {
    gene_name <- prot_info_clean$gene[i]
    prot_info_lookup$set(
      gene_name,
      list(
        accession = prot_info_clean$accession[i],
        url = prot_info_clean$url[i]
      )
    )
  }

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

  gwas_trait_mapping <- setNames(
    gwas_trait_names$full_name,
    gwas_trait_names$abbrev
  )

  # Pre-compute GWAS trait membership for fast filtering (using fastmap)
  # Vectorized: expand list column to long format, then split by trait
  gwas_trait_rows <- fastmap::fastmap()
  gwas_col <- table1$`GWAS Trait`
  n_rows <- length(gwas_col)

  # Build expanded data.table: row_id -> trait mapping
  gwas_expanded <- data.table::rbindlist(lapply(seq_len(n_rows), function(i) {
    traits <- gwas_col[[i]]
    if (is.list(traits)) traits <- traits[[1L]]
    if (length(traits) == 0L || is.null(traits) ||
          (length(traits) == 1L && (is.na(traits[1L]) ||
                                      traits[1L] == PLACEHOLDER_NONE_FOUND))) {
      data.table::data.table(row_id = i, trait = PLACEHOLDER_NONE_FOUND)
    } else {
      data.table::data.table(row_id = i, trait = traits)
    }
  }))

  # Split row indices by trait (vectorized grouping)
  gwas_split <- split(gwas_expanded$row_id, gwas_expanded$trait)
  for (trait in names(gwas_split)) {
    gwas_trait_rows$set(trait, gwas_split[[trait]])
  }

  # Pre-compute omics type membership for fast filtering (using fastmap)
  # Vectorized: expand list column to long format, then split by type
  omics_type_rows <- fastmap::fastmap()
  omics_col <- table1$`Evidence From Other Omics Studies`

  # Build expanded data.table: row_id -> omics_type mapping
  omics_expanded <- data.table::rbindlist(lapply(seq_len(n_rows), function(i) {
    omics_evidence <- omics_col[[i]]
    if (is.list(omics_evidence)) omics_evidence <- omics_evidence[[1L]]
    if (length(omics_evidence) == 0L || is.null(omics_evidence) ||
          (length(omics_evidence) == 1L &&
             (is.na(omics_evidence[1L]) ||
                omics_evidence[1L] == PLACEHOLDER_NONE_FOUND))) {
      data.table::data.table(row_id = i, omics_type = PLACEHOLDER_NONE_FOUND)
    } else {
      # Extract omics type prefix (before semicolon)
      omics_types <- vapply(omics_evidence, function(x) sub(";.*", "", x),
                            character(1L))
      data.table::data.table(row_id = i, omics_type = omics_types)
    }
  }))

  # Split row indices by omics type (vectorized grouping)
  omics_split <- split(omics_expanded$row_id, omics_expanded$omics_type)
  for (omics_type in names(omics_split)) {
    omics_type_rows$set(omics_type, omics_split[[omics_type]])
  }

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
    prot_info_lookup = prot_info_lookup,
    omics_df = omics_df,
    unique_gwas_traits = unique_gwas_traits,
    gwas_traits_df = gwas_traits_df,
    gwas_trait_mapping = gwas_trait_mapping,
    gwas_trait_rows = gwas_trait_rows,
    omics_type_rows = omics_type_rows,
    omim_lookup = omim_lookup,
    refs = refs
  )
}

# Load and prepare Table 2 clinical trial data
#
# Lazily loads clinical trial data and associated gene information.
# Processes registry IDs, formats completion dates, and prepares
# trial metadata for display.
#
# Details:
#   This function performs the following operations:
#   - Loads table2_clean.RDS and gene_info_table2.RDS
#   - Renames gene info columns to match Table 1 format
#   - Extracts trial name and primary outcome into ct_info
#   - Converts completion dates to "Month Year" format
#   - Identifies clinical trial registry types (NCT, ISRCTN, ACTRN, ChiCTR)
#
# Returns:
#   A named list containing:
#   - table2: Clinical trial data as a data.table
#   - ct_info: Trial name and primary outcome for tooltips
#   - registry_matches: Data frame of matched registry patterns
#   - registry_rows: Fastmap of registry pattern to row indices
#   - gene_info_table2: NCBI gene info for Table 2 genes
#   - gene_symbols_table2: Character vector of gene symbols
#   - sample_sizes: Numeric vector of sample sizes for histogram
#   - sample_sizes_hash: Pre-computed digest hash for caching
load_table2_data <- function() {
  message("Loading Table 2 clinical trial data...")
  table2 <- safe_read_rds(DATA_PATHS$table2_clean)

  message("Loading Table 2 gene info...")
  gene_info_table2 <- safe_read_rds(DATA_PATHS$gene_info_table2)

  # Rename columns and add URL (same as Table 1 gene info)
  gene_info_table2 <- prepare_gene_info_df(gene_info_table2)

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

  table2$`Estimated Completion Date` <- convert_month_year_date(
    table2$`Estimated Completion Date`
  )

  # Mark unpublished completed trials (have a drug but no completion date)
  unpublished_idx <- which(
    is.na(table2$`Estimated Completion Date`) &
      !is.na(table2$Drug)
  )
  if (length(unpublished_idx) > 0L) {
    table2$`Estimated Completion Date`[unpublished_idx] <- COMPLETION_UNPUBLISHED
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

# nolint end: object_usage_linter.
