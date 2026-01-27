# read_external_data.R
# Functions to read external data (NCBI, UniProt, PubMed) from database cache
# These replace the API-calling functions that were previously in:
# - fetch_ncbi_gene_data.R
# - fetch_uniprot_data.R
# - fetch_pubmed_data.R

# Note: with_db_connection is defined in utils.R and sourced at runtime

# =============================================================================
# NCBI GENE INFO
# =============================================================================

# Read NCBI Gene Info from Database
#
# Retrieves cached NCBI gene information for the specified gene symbols.
# Data is populated by the Python pipeline (main.py --sync-external-data).
#
# Args:
#   gene_symbols: Character vector of gene symbols to look up.
#   con: Optional existing DBI connection. If NULL, a new connection is created.
#   ...: Additional arguments passed to with_db_connection.
#
# Returns:
#   Data frame with columns: name, uid, description, otheraliases
#   Matching the format expected by the dashboard.
read_ncbi_gene_info_from_db <- function(gene_symbols, con = NULL, ...) {
  if (length(gene_symbols) == 0) {
    return(data.frame(
      name = character(0),
      uid = character(0),
      description = character(0),
      otheraliases = character(0),
      stringsAsFactors = FALSE
    ))
  }

  with_db_connection(function(conn) { # nolint: object_usage_linter.
    # Build parameterized query
    placeholders <- paste0("$", seq_along(gene_symbols), collapse = ", ")
    query <- sprintf(
      "SELECT gene_symbol as name, ncbi_uid as uid,
              description, aliases as otheraliases
       FROM ncbi_gene_info
       WHERE gene_symbol IN (%s)",
      placeholders
    )

    result <- DBI::dbGetQuery(
      conn,
      query,
      params = as.list(gene_symbols)
    )

    # Ensure all requested genes are in result (with NA for missing)
    missing_genes <- setdiff(gene_symbols, result$name)
    if (length(missing_genes) > 0) {
      missing_df <- data.frame(
        name = missing_genes,
        uid = NA_character_,
        description = NA_character_,
        otheraliases = NA_character_,
        stringsAsFactors = FALSE
      )
      result <- rbind(result, missing_df)
    }

    # Reorder to match input order
    result[match(gene_symbols, result$name), ]
  }, con = con, ...)
}


# Read NCBI Gene Info for Table 2 Genes from Database
#
# Extracts gene symbols from the clinical trials table and retrieves
# their NCBI gene information from the cache.
#
# Args:
#   con: Optional existing DBI connection.
#   ...: Additional arguments passed to with_db_connection.
#
# Returns:
#   Data frame with columns: name, uid, description, otheraliases
read_table2_ncbi_info_db <- function(con = NULL, ...) {
  # First, get gene symbols from clinical_trials table
  genes <- with_db_connection(function(conn) { # nolint: object_usage_linter.
    result <- DBI::dbGetQuery(
      conn,
      "SELECT DISTINCT genetic_target FROM clinical_trials
       WHERE genetic_target IS NOT NULL"
    )

    # Extract individual genes from comma-separated lists
    all_genes <- character(0)
    for (target in result$genetic_target) {
      if (!is.na(target) && nchar(target) > 0) {
        # Split by comma, semicolon, or slash
        genes <- unlist(strsplit(target, "[,;/]"))
        genes <- trimws(genes)
        genes <- genes[genes != "" & toupper(genes) != "NA" & genes != "-"]
        all_genes <- c(all_genes, genes)
      }
    }
    unique(all_genes)
  }, con = con, ...)

  if (length(genes) == 0) {
    return(data.frame(
      name = character(0),
      uid = character(0),
      description = character(0),
      otheraliases = character(0),
      stringsAsFactors = FALSE
    ))
  }

  # Now fetch NCBI info for these genes
  read_ncbi_gene_info_from_db(genes, con = con, ...)
}


# =============================================================================
# UNIPROT DATA
# =============================================================================

# Read UniProt Data from Database
#
# Retrieves cached UniProt protein information for the specified gene symbols.
# Data is populated by the Python pipeline (main.py --sync-external-data).
#
# Args:
#   gene_symbols: Character vector of gene symbols to look up.
#   con: Optional existing DBI connection.
#   ...: Additional arguments passed to with_db_connection.
#
# Returns:
#   Data frame with columns: gene, accession, biological_process,
#   molecular_function, cellular_component, url, protein_name
#   Matching the format expected by the dashboard.
read_uniprot_data_from_db <- function(gene_symbols, con = NULL, ...) {
  if (length(gene_symbols) == 0) {
    return(data.frame(
      gene = character(0),
      accession = character(0),
      biological_process = character(0),
      molecular_function = character(0),
      cellular_component = character(0),
      url = character(0),
      protein_name = character(0),
      stringsAsFactors = FALSE
    ))
  }

  with_db_connection(function(conn) { # nolint: object_usage_linter.
    placeholders <- paste0("$", seq_along(gene_symbols), collapse = ", ")
    query <- sprintf(
      "SELECT gene_symbol as gene, accession, protein_name,
              biological_process, molecular_function, cellular_component, url
       FROM uniprot_info
       WHERE gene_symbol IN (%s)",
      placeholders
    )

    result <- DBI::dbGetQuery(
      conn,
      query,
      params = as.list(gene_symbols)
    )

    # Ensure all requested genes are in result (with NA for missing)
    missing_genes <- setdiff(gene_symbols, result$gene)
    if (length(missing_genes) > 0) {
      missing_df <- data.frame(
        gene = missing_genes,
        accession = NA_character_,
        protein_name = NA_character_,
        biological_process = NA_character_,
        molecular_function = NA_character_,
        cellular_component = NA_character_,
        url = NA_character_,
        stringsAsFactors = FALSE
      )
      result <- rbind(result, missing_df)
    }

    # Reorder to match input order
    result[match(gene_symbols, result$gene), ]
  }, con = con, ...)
}


# =============================================================================
# PUBMED REFERENCES
# =============================================================================

# Read PubMed References from Database
#
# Retrieves cached PubMed citation information for the specified PMIDs.
# Data is populated by the Python pipeline (main.py --sync-external-data).
#
# Args:
#   pmids: Character vector of PubMed IDs to look up.
#   con: Optional existing DBI connection.
#   ...: Additional arguments passed to with_db_connection.
#
# Returns:
#   Data frame with columns: pmid, formatted_ref
#   Matching the format expected by the dashboard.
read_pubmed_refs_from_db <- function(pmids, con = NULL, ...) {
  if (length(pmids) == 0) {
    return(data.frame(
      pmid = character(0),
      formatted_ref = character(0),
      stringsAsFactors = FALSE
    ))
  }

  # Ensure PMIDs are strings
  pmids <- as.character(pmids)

  with_db_connection(function(conn) { # nolint: object_usage_linter.
    placeholders <- paste0("$", seq_along(pmids), collapse = ", ")
    query <- sprintf(
      "SELECT pmid, formatted_ref FROM pubmed_citations WHERE pmid IN (%s)",
      placeholders
    )

    result <- DBI::dbGetQuery(
      conn,
      query,
      params = as.list(pmids)
    )

    # Ensure all requested PMIDs are in result (with placeholder for missing)
    missing_pmids <- setdiff(pmids, result$pmid)
    if (length(missing_pmids) > 0) {
      missing_df <- data.frame(
        pmid = missing_pmids,
        formatted_ref = paste0(
          "PMID: ", missing_pmids, " (citation not available)"
        ),
        stringsAsFactors = FALSE
      )
      result <- rbind(result, missing_df)
    }

    # Reorder to match input order
    result[match(pmids, result$pmid), ]
  }, con = con, ...)
}
