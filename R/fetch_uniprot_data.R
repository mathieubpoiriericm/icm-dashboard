# fetch_uniprot_data.R
# UniProt data fetching and cleaning functions

#' Fetch UniProt Accession IDs
#'
#' Fetches UniProt accession IDs and protein names for a vector of gene symbols.
#'
#' @param genes A character vector of gene symbols.
#' @param organism NCBI taxonomy ID. Defaults to "9606" (Homo sapiens).
#'
#' @return A data.frame with columns: gene, accession, protein_name.
#'
#' @export
fetch_uniprot_accessions <- function(genes, organism = "9606") {
  base_url <- "https://rest.uniprot.org/uniprotkb/search"

  results <- lapply(genes, function(gene) {
    # UniProt query:
    # gene_exact matches official names
    # gene matches synonyms
    query_string <- sprintf(
      '(gene_exact:"%s" OR gene:"%s") AND (organism_id:%s)',
      gene,
      gene,
      organism
    )

    resp <- tryCatch(
      {
        httr2::request(base_url) |>
          httr2::req_url_query(
            query = query_string,
            format = "tsv",
            fields = "accession,gene_primary,gene_synonym,protein_name",
            size = 5L
          ) |>
          httr2::req_perform()
      },
      error = function(e) NULL
    )

    if (is.null(resp) || httr2::resp_status(resp) != 200L) {
      status_code <- if (!is.null(resp)) {
        httr2::resp_status(resp)
      } else {
        "connection error"
      }
      warning(
        sprintf("Query failed for %s (HTTP %s)", gene, status_code),
        call. = FALSE
      )
      return(data.frame(
        gene = gene,
        accession = NA,
        protein_name = NA,
        stringsAsFactors = FALSE
      ))
    }

    txt <- httr2::resp_body_string(resp)
    result_df <- tryCatch(
      read.delim(text = txt, sep = "\t", stringsAsFactors = FALSE),
      error = function(e) NULL
    )

    if (is.null(result_df) || nrow(result_df) == 0L) {
      return(data.frame(
        gene = gene,
        accession = NA,
        protein_name = NA,
        stringsAsFactors = FALSE
      ))
    }

    # Prefer exact primary gene match
    hit <- result_df[result_df$Gene.Primary == gene, ]
    if (nrow(hit) == 0L) {
      hit <- result_df[1L, , drop = FALSE]
    }

    data.frame(
      gene = gene,
      accession = hit$Entry[1L],
      protein_name = hit$Protein.names[1L],
      stringsAsFactors = FALSE
    )
  })

  do.call(rbind, results)
}

#' Clean GO Column
#'
#' Cleans a Gene Ontology column by removing GO numbers and fixing formatting.
#'
#' @param go_column A character vector of GO annotations.
#'
#' @return A cleaned character vector.
#'
#' @export
clean_go_column <- function(go_column) {
  # Remove GO numbers in brackets

  go_column <- gsub("\\[.*?\\]", "", go_column)
  # Fix semicolon spacing

  go_column <- gsub(" ;", ",", go_column, fixed = TRUE)
  # Remove trailing whitespace
  go_column <- gsub("\\s+$", "", go_column)

  go_column
}

#' Clean Protein GO Info
#'
#' Cleans protein Gene Ontology information data.frame.
#'
#' @param prot_info A data.frame from UniprotR::GetProteinGOInfo().
#'
#' @return A cleaned data.frame with renamed and cleaned columns.
#'
#' @export
clean_protein_go_info <- function(prot_info) {
  # Remove first two columns (usually Entry and Status)
  prot_info_clean <- prot_info[, -c(1L, 2L)]

  # Rename columns
  names(prot_info_clean) <- gsub(
    "Gene.Ontology..biological.process.",
    "biological_process",
    names(prot_info_clean)
  )
  names(prot_info_clean) <- gsub(
    "Gene.Ontology..molecular.function.",
    "molecular_function",
    names(prot_info_clean)
  )
  names(prot_info_clean) <- gsub(
    "Gene.Ontology..cellular.component.",
    "cellular_component",
    names(prot_info_clean)
  )

  # Clean each GO column
  if ("biological_process" %in% names(prot_info_clean)) {
    prot_info_clean$biological_process <- clean_go_column(
      prot_info_clean$biological_process
    )
  }
  if ("molecular_function" %in% names(prot_info_clean)) {
    prot_info_clean$molecular_function <- clean_go_column(
      prot_info_clean$molecular_function
    )
  }
  if ("cellular_component" %in% names(prot_info_clean)) {
    prot_info_clean$cellular_component <- clean_go_column(
      prot_info_clean$cellular_component
    )
  }

  prot_info_clean
}

#' Generate UniProt URLs
#'
#' Generates UniProt entry URLs from accession IDs.
#'
#' @param accession_ids A character vector of UniProt accession IDs.
#'
#' @return A character vector of URLs.
#'
#' @export
generate_uniprot_urls <- function(accession_ids) {
  paste0("https://www.uniprot.org/uniprotkb/", accession_ids, "/entry")
}

#' Fetch and Process UniProt Data
#'
#' Main function that fetches UniProt accession IDs and GO information
#' for a vector of genes.
#'
#' @param genes A character vector of gene symbols.
#' @param organism NCBI taxonomy ID. Defaults to "9606" (Homo sapiens).
#' @param verbose If TRUE, prints progress messages. Defaults to FALSE.
#'
#' @return A data.frame with gene, accession, GO info, and URL columns.
#'
#' @export
fetch_uniprot_data <- function(genes, organism = "9606", verbose = FALSE) {
  if (verbose) {
    message("Fetching UniProt accession IDs...")
  }

  # Fetch accession IDs
  accession_data <- fetch_uniprot_accessions(genes, organism = organism)

  # Get valid accession IDs for GO lookup
  valid_accessions <- accession_data$accession[!is.na(accession_data$accession)]

  if (length(valid_accessions) == 0L) {
    warning("No valid accession IDs found")
    return(accession_data)
  }

  if (verbose) {
    message("Fetching protein GO information...")
  }

  # Fetch GO info
  prot_info <- UniprotR::GetProteinGOInfo(valid_accessions)

  # Clean GO info
  prot_info_clean <- clean_protein_go_info(prot_info)

  # Combine with gene and accession info
  # Match by position for genes with valid accessions
  valid_rows <- !is.na(accession_data$accession)
  result <- cbind(
    accession_data[valid_rows, c("gene", "accession")],
    prot_info_clean
  )

  # Add URLs

  result$url <- generate_uniprot_urls(result$accession)

  # Remove row names

  rownames(result) <- NULL

  result
}
