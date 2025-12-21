# fetch_ncbi_gene_data.R
# NCBI gene data fetching and cleaning functions

#' Fetch Gene Info from NCBI
#'
#' Fetches gene information from NCBI for a single gene symbol.
#' Used internally by fetch_all_gene_info().
#'
#' @param gene_symbol A character string of the gene symbol to look up.
#'
#' @return A data.frame with columns: uid, description, otheraliases.
#'
#' @export
fetch_gene_info <- function(gene_symbol) {
  tryCatch(
    {
      gene_search <- rentrez::entrez_search(
        db = "gene",
        term = paste0(gene_symbol, "[Gene Name] AND Homo sapiens[Organism]")
      )

      if (length(gene_search$ids) == 0L) {
        return(data.frame(uid = NA, description = NA, otheraliases = NA))
      }

      gene_id <- gene_search$ids[1L]

      gene_summary <- rentrez::entrez_summary(db = "gene", id = gene_id)

      data.frame(
        uid = gene_summary$uid,
        description = gene_summary$description,
        otheraliases = gene_summary$otheraliases,
        stringsAsFactors = FALSE
      )
    },
    error = function(e) {
      data.frame(
        uid = NA,
        description = NA,
        otheraliases = NA,
        stringsAsFactors = FALSE
      )
    }
  )
}

#' Fetch Gene Info for Multiple Genes
#'
#' Fetches gene information from NCBI for a vector of gene symbols.
#' Includes a delay between requests to respect NCBI rate limits.
#'
#' @param gene_symbols A character vector of gene symbols to look up.
#' @param delay Delay in seconds between API requests. Defaults to 0.1.
#' @param verbose If TRUE, prints progress messages. Defaults to FALSE.
#'
#' @return A data.frame with columns: uid, description, otheraliases.
#'
#' @export
fetch_all_gene_info <- function(gene_symbols, delay = 0.1, verbose = FALSE) {
  gene_info_results <- vector("list", length(gene_symbols))

  for (i in seq_along(gene_symbols)) {
    if (verbose) {
      message(sprintf(
        "Fetching gene %d/%d: %s",
        i,
        length(gene_symbols),
        gene_symbols[i]
      ))
    }
    gene_info_results[[i]] <- fetch_gene_info(gene_symbols[i])
    Sys.sleep(delay)
  }

  do.call(rbind, gene_info_results)
}

#' Extract Gene Symbols from Table 2
#'
#' Extracts unique gene symbols from the "Genetic Target" column of Table 2
#' and saves them to data/txt/genes_table2.txt.
#'
#' @param verbose If TRUE, prints progress messages. Defaults to TRUE.
#'
#' @return A character vector of unique gene symbols.
#'
#' @export
#' @examples
#' \dontrun{
#' source("R/fetch_ncbi_gene_data.R")
#' extract_table2_gene_symbols()
#' }
extract_table2_gene_symbols <- function(verbose = TRUE) {
  # Load table2 data
  table2 <- readRDS("data/rdata/table2_clean.rds")

  genes_raw <- table2[["Genetic Target"]]

  # Split comma-separated genes and flatten
  all_genes <- unlist(strsplit(genes_raw, ",[ ]*"))
  all_genes <- trimws(all_genes)

  # Remove empty strings, NAs, and placeholder values
  all_genes <- unique(all_genes[
    !is.na(all_genes) & all_genes != "" & all_genes != "(none)"
  ])

  # Save to file
  writeLines(all_genes, "data/txt/genes_table2.txt")

  if (verbose) {
    message(sprintf(
      "Extracted %d unique genes and saved to data/txt/genes_table2.txt",
      length(all_genes)
    ))
  }

  all_genes
}

#' Fetch and Save Table 2 Gene Info
#'
#' Extracts gene symbols from Table 2, fetches gene information from NCBI,
#' and saves the results to data/rdata/.
#'
#' @param delay Delay in seconds between API requests. Defaults to 0.1.
#' @param verbose If TRUE, prints progress messages. Defaults to TRUE.
#' @param extract_genes If TRUE, extracts genes from Table 2 first.
#'   If FALSE, reads from existing data/txt/genes_table2.txt. Defaults to TRUE.
#'
#' @return Invisibly returns the gene info data frame.
#'
#' @export
#' @examples
#' \dontrun{
#' source("R/fetch_ncbi_gene_data.R")
#' fetch_save_table2_gene_info()
#' }
fetch_save_table2_gene_info <- function(
  delay = 0.1,
  verbose = TRUE,
  extract_genes = TRUE
) {
  # Extract gene symbols from Table 2 or read from existing file
  if (extract_genes) {
    gene_symbols <- extract_table2_gene_symbols(verbose = verbose)
  } else {
    gene_symbols <- readLines("data/txt/genes_table2.txt")
    gene_symbols <- gene_symbols[gene_symbols != ""]
  }

  if (verbose) {
    message(sprintf("Fetching info for %d genes...", length(gene_symbols)))
  }

  # Fetch gene info from NCBI
  gene_info_table2 <- fetch_all_gene_info(
    gene_symbols,
    delay = delay,
    verbose = verbose
  )

  # Add gene symbol as first column for matching (same as Table 1)
  gene_info_table2 <- cbind(
    name = gene_symbols,
    gene_info_table2
  )

  # Save results
  saveRDS(gene_info_table2, file = "data/rdata/gene_info_table2.rds")

  if (verbose) {
    message("Saved to data/rdata/gene_info_table2.rds")
  }

  invisible(gene_info_table2)
}
