# phenogram.R
# Phenogram data generation for Ritchie Lab PhenoGram tool

# Import .data pronoun for tidy evaluation
.data <- rlang::.data

#' Fetch Gene Locations from Ensembl
#'
#' Retrieves chromosome locations for a vector of gene symbols using biomaRt.
#'
#' @param genes A character vector of HGNC gene symbols.
#'
#' @return A data.frame with columns: hgnc_symbol, chromosome_name,
#'   start_position, end_position.
#'
#' @export
fetch_gene_locations <- function(genes) {
  ensembl <- biomaRt::useEnsembl(
    biomart = "genes",
    dataset = "hsapiens_gene_ensembl"
  )

  results <- biomaRt::getBM(
    attributes = c(
      "hgnc_symbol",
      "chromosome_name",
      "start_position",
      "end_position"
    ),
    filters = "hgnc_symbol",
    values = genes,
    mart = ensembl
  )

  # Filter to standard chromosomes only (1-22, X, Y)
  results <- dplyr::filter(results, grepl("^[0-9]+$", .data$chromosome_name))

  results
}

#' Clean Phenotype String
#'
#' Removes unwanted characters from phenotype/GWAS trait strings.
#'
#' @param x A character vector.
#'
#' @return A cleaned character vector.
#'
#' @export
clean_phenotype_string <- function(x) {
  x <- as.character(x)
  x <- gsub("c(", "", x, fixed = TRUE)
  x <- gsub("(", "", x, fixed = TRUE)
  x <- gsub(")", "", x, fixed = TRUE)
  x <- gsub('"', "", x, fixed = TRUE)
  x
}

#' Apply Phenotype Title Casing
#'
#' Applies consistent title casing to phenotype names.
#'
#' @param phenotypes A character vector of phenotype names.
#'
#' @return A character vector with corrected casing.
#'
#' @export
fix_phenotype_casing <- function(phenotypes) {
  phenotypes <- gsub("lacunes", "Lacunes", phenotypes, fixed = TRUE)
  phenotypes <- gsub("extreme", "Extreme", phenotypes, fixed = TRUE)
  phenotypes <- gsub(
    "lacunar stroke",
    "Lacunar Stroke",
    phenotypes,
    fixed = TRUE
  )
  phenotypes <- gsub("stroke", "Stroke", phenotypes, fixed = TRUE)
  phenotypes <- trimws(phenotypes)
  phenotypes
}

#' Create Phenogram Data
#'
#' Creates a phenogram-formatted data.frame for the Ritchie Lab PhenoGram tool.
#'
#' @param table1 A cleaned table1 data.frame with Gene and GWAS Trait columns.
#' @param gene_locations Optional. Pre-fetched gene locations. If NULL,
#'   fetches from Ensembl.
#'
#' @return A list containing:
#'   - phenogram: data.frame formatted for PhenoGram
#'     (phenotype, annotation, chr, pos)
#'   - missing_genes: genes not found in Ensembl
#'   - gene_locations: the gene location data used
#'
#' @export
create_phenogram_data <- function(table1, gene_locations = NULL) {
  genes <- table1$Gene

  # Fetch gene locations if not provided
  if (is.null(gene_locations)) {
    gene_locations <- fetch_gene_locations(genes)
  }

  # Find missing genes
  missing_genes <- genes[!genes %in% gene_locations$hgnc_symbol]

  # Create base phenogram structure
  phenogram <- gene_locations
  phenogram[, 4L] <- "Insert GWAS Trait"
  phenogram <- dplyr::select(
    phenogram,
    "end_position",
    "hgnc_symbol",
    "chromosome_name",
    "start_position"
  )

  names(phenogram)[1L] <- "GWAS Trait"
  names(phenogram)[2L] <- "Gene"

  # Subset table1 for genes with locations

  table1_subset <- table1[table1$Gene %in% gene_locations$hgnc_symbol, ]
  table1_subset <- dplyr::mutate(
    table1_subset,
    dplyr::across(
      tidyselect::where(is.list),
      ~ vapply(., paste, character(1L), collapse = ", ")
    )
  )

  # Join GWAS traits
  phenogram <- phenogram |>
    dplyr::left_join(
      dplyr::select(table1_subset, "Gene", "GWAS Trait"),
      by = "Gene",
      suffix = c("", ".new")
    ) |>
    dplyr::mutate(
      `GWAS Trait` = dplyr::coalesce(.data$`GWAS Trait.new`, .data$`GWAS Trait`)
    ) |>
    dplyr::select(-"GWAS Trait.new")

  # Rename columns to PhenoGram format
  names(phenogram) <- c("phenotype", "annotation", "chr", "pos")

  # Fix phenotype casing
  phenogram$phenotype <- fix_phenotype_casing(phenogram$phenotype)

  list(
    phenogram = phenogram,
    missing_genes = missing_genes,
    gene_locations = gene_locations
  )
}

#' Extract Unique Phenotypes
#'
#' Extracts unique phenotype values from table1 GWAS Trait column.
#'
#' @param table1 A cleaned table1 data.frame.
#' @param exclude_genes Optional character vector of genes to exclude.
#'
#' @return A character vector of unique phenotypes.
#'
#' @export
extract_unique_phenotypes <- function(table1, exclude_genes = NULL) {
  filtered_table <- table1

  if (!is.null(exclude_genes)) {
    for (gene in exclude_genes) {
      filtered_table <- dplyr::filter(filtered_table, .data$Gene != gene)
    }
  }

  gwas_traits <- filtered_table$`GWAS Trait`
  gwas_traits <- clean_phenotype_string(gwas_traits)
  gwas_traits <- gsub(" ", "", gwas_traits, fixed = TRUE)

  unique(unlist(stringr::str_split(gwas_traits, stringr::fixed(","))))
}

#' Format RGBA Values for Python Script
#'
#' Formats phenotype RGBA color values for use in Python visualization.
#'
#' @param rgba_df A data.frame with columns: phenotype, rgba_value.
#'
#' @return A data.frame with formatted columns for Python script.
#'
#' @export
format_rgba_for_python <- function(rgba_df) {
  rgba_df <- rgba_df[, c("rgba_value", "phenotype")]
  rgba_df$rgba_value <- paste0('"', rgba_df$rgba_value, '"')
  rgba_df$phenotype <- paste0('"', rgba_df$phenotype, '"')
  rgba_df$combined <- paste(
    rgba_df$rgba_value,
    rgba_df$phenotype,
    sep = ": "
  )

  rgba_df
}

#' Export Phenogram Data
#'
#' Exports phenogram data to a tab-separated file.
#'
#' @param phenogram A phenogram data.frame.
#' @param file_path Output file path. Defaults to "phenogram.txt".
#'
#' @export
export_phenogram <- function(phenogram, file_path = "phenogram.txt") {
  write.table(
    phenogram,
    file = file_path,
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )

  message(sprintf("Exported phenogram to: %s", file_path))
}

#' Generate Complete Phenogram Output
#'
#' Main function that generates all phenogram-related outputs.
#'
#' @param table1 A cleaned table1 data.frame.
#' @param export If TRUE, exports files. Defaults to FALSE.
#' @param output_dir Directory for output files. Defaults to current directory.
#'
#' @return A list containing phenogram data and related outputs.
#'
#' @export
generate_phenogram <- function(table1, export = FALSE, output_dir = ".") {
  # Create phenogram data
  result <- create_phenogram_data(table1)

  # Extract unique phenotypes
  unique_phenotypes <- extract_unique_phenotypes(
    table1,
    exclude_genes = result$missing_genes
  )

  result$unique_phenotypes <- unique_phenotypes

  # Export if requested
  if (export) {
    export_phenogram(
      result$phenogram,
      file.path(output_dir, "phenogram.txt")
    )

    readr::write_csv(
      data.frame(phenotype = unique_phenotypes),
      file = file.path(output_dir, "unique_phenotypes.csv"),
      col_names = FALSE
    )
  }

  result
}
