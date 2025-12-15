# tooltips.R
# Tooltip generation functions for table displays
# nolint start: object_usage_linter.

# Memoised tooltip generators for repeated lookups across sessions
# Uses in-memory cache (faster than disk for session-scoped data)
memo_cache <- cachem::cache_mem(max_size = MEMO_CACHE_SIZE)

#' Get full cell type name from abbreviation
#'
#' Maps brain cell type abbreviations to their full names for tooltip display.
#'
#' @param cell_abbrev Character. Cell type abbreviation (e.g., "EC", "MG").
#'
#' @return Character. Full cell type name (e.g., "Endothelial Cells").
#'
#' @export
#' @examples
#' get_cell_type_tooltip("EC")
#' # Returns: "Endothelial Cells"
get_cell_type_tooltip <- function(cell_abbrev) {
  if (cell_abbrev %in% names(CELL_TYPE_MAP)) {
    CELL_TYPE_MAP[[cell_abbrev]]
  } else {
    paste0("Full name for ", cell_abbrev)
  }
}

#' Add tooltip HTML to cell type string
#'
#' Parses a cell type string containing abbreviations separated by
#' comparison operators (< or >) and wraps each abbreviation in a
#' Tippy.js tooltip span.
#'
#' @param cell_types_string Character. Cell types separated by < or >
#'   (e.g., "EC > MG > AC").
#' @param tooltip_style Character. CSS style string for the tooltip span.
#'
#' @return Character. HTML string with Tippy tooltip data attributes.
#'
#' @export
#' @seealso \code{\link{get_cell_type_tooltip}}
add_cell_type_tooltip <- function(cell_types_string, tooltip_style) {
  if (
    is.na(cell_types_string) ||
      cell_types_string == "" ||
      cell_types_string == PLACEHOLDER_UNKNOWN
  ) {
    return(PLACEHOLDER_UNKNOWN)
  }

  separators <- stringr::str_extract_all(cell_types_string, "[<>]")[[1L]]
  cell_types <- stringr::str_split(cell_types_string, "[<>]")[[1L]]
  cell_types <- trimws(cell_types)

  html_cell_type <- vapply(
    cell_types,
    function(cell_type) {
      if (tolower(cell_type) == "all" || cell_type == "") {
        return(cell_type)
      }

      tooltip_text <- get_cell_type_tooltip(cell_type)

      as.character(shiny::tags$span(
        `data-tippy-content` = tooltip_text,
        style = tooltip_style,
        cell_type
      ))
    },
    character(1L)
  )

  if (length(separators) > 0L) {
    result <- html_cell_type[1L]
    for (i in seq_along(separators)) {
      result <- paste0(
        result,
        " ",
        separators[i],
        " ",
        html_cell_type[i + 1L]
      )
    }
    result
  } else {
    html_cell_type[1L]
  }
}

#' Get reference tooltip information by PMID
#'
#' Looks up publication information from the refs data frame for a given PMID.
#'
#' @param pmid_hover Character. The PubMed ID to look up.
#' @param refs Data frame. Reference data with PMID in column 1 and
#'   tooltip HTML in column 2.
#'
#' @return Character. HTML tooltip content or "No info available".
#'
#' @export
get_ref_tooltip_info <- function(pmid_hover, refs) {
  match_row <- refs[refs[[1L]] == pmid_hover, ]

  if (nrow(match_row) > 0L) {
    as.character(match_row[[2L]])
  } else {
    "No info available"
  }
}

#' Memoised version of get_ref_tooltip_info
#'
#' Caches reference tooltip lookups to avoid repeated data frame searches.
#' The cache persists for the session lifetime.
#'
#' @param pmid_hover Character. The PubMed ID to look up.
#' @param refs Data frame. Reference data with PMID in column 1 and
#'   tooltip HTML in column 2.
#'
#' @return Character. HTML tooltip content or "No info available".
#'
#' @export
get_ref_tooltip_info_memo <- memoise::memoise(
  get_ref_tooltip_info,
  cache = memo_cache
)

#' Add reference tooltip with PubMed link
#'
#' Creates HTML anchor tags linking to PubMed with Tippy.js tooltips
#' showing publication details (title, journal, authors).
#'
#' @param split_pmid List. List containing character vector of PMIDs.
#' @param refs Data frame. Reference data for tooltip lookup.
#' @param tooltip_style Character. CSS style string for tooltip spans.
#'
#' @return Character. HTML string with linked references separated by <br>.
#'
#' @export
#' @seealso \code{\link{get_ref_tooltip_info}}
add_ref_tooltip <- function(split_pmid, refs, tooltip_style) {
  split_pmid <- split_pmid[[1L]]

  html_parts <- vapply(
    split_pmid,
    function(pmid) {
      # Use memoised version for faster repeated lookups
      ref_tooltip_text <- get_ref_tooltip_info_memo(pmid, refs)

      authors <- ""
      if (grepl("Authors:", ref_tooltip_text)) {
        authors_match <- regmatches(
          ref_tooltip_text,
          regexpr("<strong>Authors:\\s*</strong>.*?<br>", ref_tooltip_text)
        )
        if (length(authors_match) > 0L) {
          authors <- gsub(
            "<strong>Authors:\\s*</strong>",
            "",
            authors_match
          )
          authors <- gsub("<br>", "", authors, fixed = TRUE)
          authors <- gsub("<[^>]+>", "", authors)
          authors <- trimws(authors)
        }
      }

      pub_date <- ""
      if (grepl("Publication Date:", ref_tooltip_text)) {
        pub_date_match <- regmatches(
          ref_tooltip_text,
          regexpr(
            "<strong>Publication Date:\\s*</strong>.*?<br>",
            ref_tooltip_text
          )
        )
        if (length(pub_date_match) > 0L) {
          pub_date_raw <- gsub(
            "<strong>Publication Date:\\s*</strong>",
            "",
            pub_date_match
          )
          pub_date_raw <- gsub("<br>", "", pub_date_raw, fixed = TRUE)
          pub_date_raw <- gsub("<[^>]+>", "", pub_date_raw)
          pub_date_raw <- trimws(pub_date_raw)

          if (grepl("\\d{4}-\\w+", pub_date_raw)) {
            parts <- strsplit(pub_date_raw, "-")[[1L]]
            if (length(parts) == 2L) {
              pub_date <- paste0(" (", parts[2L], " ", parts[1L], ")")
            }
          }
        }
      }

      modified_tooltip <- sub(
        "<strong>Authors:\\s*</strong>.*?<br>",
        paste0("<strong>PMID</strong> ", pmid, "<br>"),
        ref_tooltip_text
      )
      modified_tooltip <- sub(
        "<strong>Publication Date:\\s*</strong>.*?<br>",
        "",
        modified_tooltip
      )

      pmid_url <- paste0("https://pubmed.ncbi.nlm.nih.gov/", pmid, "/")

      if (authors != "") {
        display_text <- paste0(authors, pub_date)
        display_text <- gsub(
          "et al\\.",
          "<i class='et-al'>et al.</i>",
          display_text
        )
      } else {
        display_text <- pmid
      }

      as.character(shiny::tags$a(
        href = pmid_url,
        target = "_blank",
        shiny::tags$span(
          `data-tippy-content` = modified_tooltip,
          `data-tippy-maxWidth` = "400px",
          style = tooltip_style,
          shiny::HTML(display_text)
        )
      ))
    },
    character(1L)
  )

  paste(html_parts, collapse = "<br>")
}

#' Prepare Table 1 display with pre-computed tooltips
#'
#' Transforms table1 data by adding Tippy.js tooltip HTML to all columns
#' that require interactive tooltips: gene symbols, proteins, OMIM IDs,
#' cell types, references, GWAS traits, and omics evidence.
#'
#' @param table1 Data frame or data.table. Main gene data table.
#' @param gene_info_results_df Data frame. NCBI gene info with URL column.
#' @param prot_info_clean Data frame. UniProt protein info with accession
#'   and URL.
#' @param omim_lookup fastmap. Pre-computed OMIM lookup map for O(1) access.
#' @param refs Data frame. Publication references for tooltip content.
#' @param omics_df Data frame. Omics types with full names.
#' @param gwas_trait_mapping Named character vector. Maps trait abbreviations
#'   to full names.
#' @param tooltip_style Character. CSS style for standard tooltips.
#' @param tooltip_style_italic Character. CSS style for italic tooltips
#'   (gene symbols).
#'
#' @return Data frame. table1 with HTML tooltip markup in display columns.
#'
#' @export
#' @seealso \code{\link{prepare_table2_display}}
prepare_table1_display <- function(
  table1,
  gene_info_results_df,
  prot_info_clean,
  omim_lookup,
  refs,
  omics_df,
  gwas_trait_mapping,
  tooltip_style,
  tooltip_style_italic
) {
  table1_display <- as.data.frame(table1)

  # Vectorized gene symbol tooltip
  # Store original gene names for sorting before HTML transformation
  original_gene_names <- table1_display[[1L]]

  table1_display[[1L]] <- purrr::pmap_chr(
    list(
      table1_display[[1L]],
      seq_len(nrow(table1_display)),
      original_gene_names
    ),
    function(gene_symbol, i, sort_name) {
      tooltip_content <- paste0(
        "<strong>",
        names(gene_info_results_df)[1L],
        "</strong> ",
        gene_info_results_df[i, 1L],
        "<br>",
        "<strong>",
        names(gene_info_results_df)[2L],
        "</strong> ",
        gene_info_results_df[i, 2L],
        "<br>",
        "<strong>",
        names(gene_info_results_df)[3L],
        "</strong> ",
        gene_info_results_df[i, 3L]
      )

      gene_url <- gene_info_results_df[i, "URL"]

      as.character(
        shiny::tags$span(
          `data-order` = tolower(sort_name),
          shiny::tags$a(
            href = gene_url,
            target = "_blank",
            shiny::tags$span(
              gene_symbol,
              `data-tippy-content` = tooltip_content,
              style = tooltip_style_italic
            )
          )
        )
      )
    }
  )

  # Vectorized protein tooltip
  # Store original protein names for sorting before HTML transformation
  original_protein_names <- table1_display[[2L]]

  table1_display[[2L]] <- purrr::pmap_chr(
    list(
      table1_display[[2L]],
      as.data.frame(table1)[[1L]],
      original_protein_names
    ),
    function(protein_name, gene_name, sort_name) {
      prot_match <- prot_info_clean[prot_info_clean$gene == gene_name, ]

      if (nrow(prot_match) > 0L) {
        accession_number <- prot_match$accession
        protein_url <- prot_match$url

        protein_tooltip_content <- paste0(
          "<strong>UniProt Accession Number</strong> ",
          accession_number
        )

        as.character(
          shiny::tags$span(
            `data-order` = tolower(sort_name),
            shiny::tags$a(
              href = protein_url,
              target = "_blank",
              shiny::tags$span(
                protein_name,
                `data-tippy-content` = protein_tooltip_content,
                style = tooltip_style
              )
            )
          )
        )
      } else {
        as.character(
          shiny::tags$span(
            `data-order` = tolower(sort_name),
            protein_name
          )
        )
      }
    }
  )

  # Vectorized OMIM tooltip (using fastmap for O(1) lookups)
  table1_display[[7L]] <- purrr::map_chr(
    table1_display[[7L]],
    function(omim_value) {
      is_empty <- is.null(omim_value) || length(omim_value) == 0L
      is_single_na <- length(omim_value) == 1L && is.na(omim_value[1L])
      is_single_placeholder <- length(omim_value) == 1L &&
        omim_value[1L] %in% c("(none found)", "")

      if (is_empty || is_single_na || is_single_placeholder) {
        return(omim_value)
      }

      if (length(omim_value) > 1L) {
        omim_numbers <- trimws(as.character(omim_value))
      } else {
        omim_numbers <- trimws(strsplit(as.character(omim_value), ",")[[1L]])
      }

      omim_html_parts <- vapply(
        omim_numbers,
        function(single_omim) {
          # Use fastmap O(1) lookup instead of dplyr::filter
          info <- omim_lookup$get(single_omim)

          if (!is.null(info)) {
            tooltip_content <- paste0(
              "<strong>Phenotype</strong> ",
              info$phenotype,
              "<br>",
              "<strong>Inheritance</strong> ",
              info$inheritance,
              "<br>",
              "<strong>Gene/Locus</strong> ",
              info$gene_or_locus,
              "<br>",
              "<strong>Gene/Locus OMIM</strong> ",
              info$gene_or_locus_mim_number
            )

            as.character(
              shiny::tags$a(
                href = info$omim_link,
                target = "_blank",
                shiny::tags$span(
                  single_omim,
                  `data-tippy-content` = tooltip_content,
                  style = tooltip_style
                )
              )
            )
          } else {
            single_omim
          }
        },
        character(1L)
      )

      paste(omim_html_parts, collapse = "<br>")
    }
  )

  # Brain cell type tooltip (vectorized)
  table1_display[[8L]] <- vapply(
    table1_display[[8L]],
    add_cell_type_tooltip,
    character(1L),
    tooltip_style = tooltip_style
  )

  # References tooltip (vectorized)
  table1_display[[10L]] <- vapply(
    seq_len(nrow(table1_display)),
    function(i) {
      add_ref_tooltip(table1_display[i, 10L], refs, tooltip_style)
    },
    character(1L)
  )

  # GWAS Trait tooltip (vectorized)
  table1_df <- as.data.frame(table1)

  table1_display[["GWAS Trait"]] <- purrr::map_chr(
    table1_df[["GWAS Trait"]],
    function(gwas_traits) {
      if (is.list(gwas_traits)) {
        gwas_traits <- gwas_traits[[1L]]
      }
      if (length(gwas_traits) == 0L || is.na(gwas_traits[1L])) {
        return(PLACEHOLDER_NONE_FOUND)
      }

      gwas_traits_html <- vapply(
        gwas_traits,
        function(trait) {
          trait_trimmed <- trimws(trait)
          trait_display <- if (trait_trimmed %in% GWAS_TRAITS_TITLE_CASE) {
            tools::toTitleCase(trait_trimmed)
          } else {
            trait_trimmed
          }

          if (trait_trimmed %in% names(gwas_trait_mapping)) {
            full_name <- gwas_trait_mapping[[trait_trimmed]]
            as.character(shiny::tags$span(
              `data-tippy-content` = full_name,
              style = tooltip_style,
              trait_display
            ))
          } else {
            trait_display
          }
        },
        character(1L)
      )

      paste(gwas_traits_html, collapse = ", ")
    }
  )

  # Evidence From Other Omics Studies tooltip (vectorized)
  table1_display[["Evidence From Other Omics Studies"]] <- purrr::map_chr(
    table1_df[["Evidence From Other Omics Studies"]],
    function(omics_evidence) {
      if (is.list(omics_evidence)) {
        omics_evidence <- omics_evidence[[1L]]
      }
      if (length(omics_evidence) == 0L || is.na(omics_evidence[1L])) {
        return(PLACEHOLDER_NONE_FOUND)
      }

      omics_evidence_html <- vapply(
        omics_evidence,
        function(item) {
          if (grepl(";", item, fixed = TRUE)) {
            parts <- strsplit(item, ";", fixed = TRUE)[[1L]]
            parts <- trimws(parts)
            omics_type <- parts[1L]

            full_name_match <- omics_df[
              omics_df$Omics_Type == omics_type,
              "Full_Name"
            ]

            omics_type_html <- if (
              length(full_name_match) > 0L && !is.na(full_name_match)
            ) {
              as.character(shiny::tags$span(
                `data-tippy-content` = full_name_match,
                style = tooltip_style,
                omics_type
              ))
            } else {
              omics_type
            }

            if (length(parts) == 2L) {
              paste0(omics_type_html, " (", parts[2L], ")")
            } else {
              omics_type_html
            }
          } else {
            omics_type <- trimws(item)
            full_name_match <- omics_df[
              omics_df$Omics_Type == omics_type,
              "Full_Name"
            ]

            if (length(full_name_match) > 0L && !is.na(full_name_match)) {
              as.character(shiny::tags$span(
                `data-tippy-content` = full_name_match,
                style = tooltip_style,
                omics_type
              ))
            } else if (
              omics_type == "mutation effect prediction on ncRNA transcription"
            ) {
              tools::toTitleCase(omics_type)
            } else {
              item
            }
          }
        },
        character(1L)
      )

      paste(omics_evidence_html, collapse = ", ")
    }
  )

  table1_display
}

#' Prepare Table 2 display with pre-computed tooltips
#'
#' Transforms table2 clinical trial data by adding Tippy.js tooltip HTML
#' to registry IDs (with links to trial registries) and genetic targets
#' (with NCBI gene info).
#'
#' @param table2 Data frame or data.table. Clinical trial data.
#' @param ct_info Data frame. Trial name and primary outcome for tooltips.
#' @param gene_info_results_df Data frame. NCBI gene info with URL column.
#' @param gene_symbols_lookup Character vector. Gene symbols for matching.
#'
#' @return data.table. table2 with HTML tooltip markup in display columns.
#'
#' @export
#' @seealso \code{\link{prepare_table1_display}}
prepare_table2_display <- function(
  table2,
  ct_info,
  gene_info_results_df,
  gene_symbols_lookup
) {
  table2_display <- as.data.frame(table2)

  # Helper function to generate registry URL using constants
  get_registry_url <- function(registry_id) {
    registry_id <- trimws(registry_id)
    for (pattern in names(REGISTRY_URLS)) {
      if (grepl(paste0("^", pattern), registry_id, ignore.case = TRUE)) {
        return(paste0(REGISTRY_URLS[[pattern]], registry_id))
      }
    }
    NA_character_
  }

  registry_col <- which(names(table2_display) == "Registry ID")
  table2_display[[registry_col]] <- purrr::map2_chr(
    table2_display[[registry_col]],
    seq_len(nrow(table2_display)),
    function(ct_id, row_id) {
      ct_tooltip_info <- ct_info$ct_info[[row_id]][[1L]]

      ct_tooltip_content <- paste0(
        "<strong>Trial Name</strong> ",
        ct_tooltip_info[1L],
        "<br>",
        "<strong>Primary Outcome</strong> ",
        ct_tooltip_info[2L]
      )

      registry_url <- get_registry_url(ct_id)
      ct_id_trimmed <- trimws(ct_id)

      if (!is.na(registry_url)) {
        link_html <- as.character(shiny::tags$a(
          href = registry_url,
          target = "_blank",
          rel = "noopener",
          style = "color: #347bb7;",
          ct_id_trimmed
        ))
      } else {
        link_html <- ct_id_trimmed
      }

      as.character(
        shiny::tags$span(
          shiny::HTML(link_html),
          `data-tippy-content` = ct_tooltip_content,
          `data-tippy-maxWidth` = "400px",
          style = tooltip_style
        )
      )
    }
  )

  # Genetic Target tooltip (similar to Table 1 gene tooltips)
  genetic_target_col <- which(names(table2_display) == "Genetic Target")
  if (length(genetic_target_col) > 0L) {
    table2_display[[genetic_target_col]] <- purrr::map_chr(
      table2_display[[genetic_target_col]],
      function(cell_value) {
        # Handle empty or NA values
        if (is.na(cell_value) || cell_value == "" || cell_value == "(none)") {
          return(cell_value)
        }

        # Split by comma to handle multiple genes per cell
        gene_symbols <- trimws(strsplit(cell_value, ",")[[1L]])

        # Create tooltip HTML for each gene
        gene_html_parts <- vapply(
          gene_symbols,
          function(gene_symbol) {
            # Find matching row in gene_info_results_df by gene symbol
            match_idx <- which(gene_symbols_lookup == gene_symbol)

            if (length(match_idx) > 0L) {
              match_idx <- match_idx[1L]

              tooltip_content <- paste0(
                "<strong>",
                names(gene_info_results_df)[1L],
                "</strong> ",
                gene_info_results_df[match_idx, 1L],
                "<br>",
                "<strong>",
                names(gene_info_results_df)[2L],
                "</strong> ",
                gene_info_results_df[match_idx, 2L],
                "<br>",
                "<strong>",
                names(gene_info_results_df)[3L],
                "</strong> ",
                gene_info_results_df[match_idx, 3L]
              )

              gene_url <- gene_info_results_df[match_idx, "URL"]

              as.character(
                shiny::tags$a(
                  href = gene_url,
                  target = "_blank",
                  shiny::tags$span(
                    gene_symbol,
                    `data-tippy-content` = tooltip_content,
                    style = tooltip_style_italic
                  )
                )
              )
            } else {
              # No match found, return gene symbol as-is
              gene_symbol
            }
          },
          character(1L)
        )

        # Group genes 3 per line
        n_genes <- length(gene_html_parts)
        if (n_genes <= 3L) {
          paste(gene_html_parts, collapse = " ")
        } else {
          # Split into chunks of 3
          chunks <- split(
            gene_html_parts,
            ceiling(seq_along(gene_html_parts) / 3L)
          )
          lines <- vapply(chunks, paste, character(1L), collapse = " ")
          paste(lines, collapse = "<br>")
        }
      }
    )
  }

  # Remove internal columns not meant for display
  table2_display$sample_size_numeric <- NULL
  table2_display$original_row_num <- NULL

  data.table::setDT(table2_display)
  table2_display
}
# nolint end: object_usage_linter.
