# fetch_pubmed_data.R
# PubMed reference fetching and cleaning functions

#' Fetch Publication from PubMed
#'
#' Fetches publication info from PubMed for a single PMID.
#'
#' @param pmid A character or numeric PMID.
#'
#' @return A BibEntry object from RefManageR.
#'
#' @export
fetch_pub <- function(pmid) {
  # Check if NCBI is reachable before attempting to fetch
  ncbi_available <- tryCatch(
    {
      resp <- httr2::request("https://eutils.ncbi.nlm.nih.gov/") |>
        httr2::req_timeout(5) |>
        httr2::req_perform()
      httr2::resp_status(resp) == 200L
    },
    error = function(e) FALSE
  )

  if (interactive() && ncbi_available) {
    ref <- RefManageR::ReadPubMed(query = as.character(pmid))
  }

  ref
}

#' Parse XML Reference File
#'
#' Parses a MODS XML file and extracts formatted reference information.
#'
#' @param xml_path Path to the XML file.
#'
#' @return A character string with HTML-formatted reference info.
#'
#' @export
parse_reference_xml <- function(xml_path) {
  xml_file <- xml2::read_xml(xml_path)

  # Set the XML namespace
  ns <- c(mods = "http://www.loc.gov/mods/v3")

  # Extract authors
  given_names <- xml2::xml_text(xml2::xml_find_all(
    xml_file,
    ".//mods:name[@type='personal']/mods:namePart[@type='given']",
    ns
  ))
  family_names <- xml2::xml_text(xml2::xml_find_all(
    xml_file,
    ".//mods:name[@type='personal']/mods:namePart[@type='family']",
    ns
  ))
  authors <- paste(given_names, family_names)

  # Check number of authors (add "et al." if more than three)
  if (length(authors) > 3L) {
    authors <- paste0(
      "<strong>Authors: </strong>",
      authors[1L],
      "<i> et al.</i>"
    )
  } else {
    authors <- paste0(
      "<strong>Authors: </strong>",
      given_names,
      family_names,
      collapse = ", "
    )
  }

  # Extract title
  title <- xml2::xml_text(xml2::xml_find_first(
    xml_file,
    ".//mods:titleInfo/mods:title",
    ns
  ))
  title <- paste0("<strong>Title: </strong>", tools::toTitleCase(title))

  # Extract journal name
  journal <- xml2::xml_text(xml2::xml_find_first(
    xml_file,
    ".//mods:relatedItem[@type='host']/mods:titleInfo/mods:title",
    ns
  ))
  journal <- gsub(".", "", journal, fixed = TRUE)
  journal <- paste0("<strong>Journal: </strong>", tools::toTitleCase(journal))

  # Extract publication date
  pub_date <- xml2::xml_text(xml2::xml_find_first(
    xml_file,
    ".//mods:originInfo/mods:dateIssued",
    ns
  ))
  pub_date <- paste0("<strong>Publication Date: </strong>", pub_date)

  # Extract DOI
  doi <- xml2::xml_text(xml2::xml_find_first(
    xml_file,
    ".//mods:identifier[@type='doi']",
    ns
  ))
  doi <- paste0("<strong>DOI: </strong>", doi)

  # Combine everything with line breaks
  paste(authors, title, journal, pub_date, doi, sep = "<br>")
}

#' Extract Unique PMIDs from Table1
#'
#' Extracts and cleans unique PMIDs from a table1 References column.
#'
#' @param references A list column of reference PMIDs.
#'
#' @return A character vector of unique PMIDs.
#'
#' @export
extract_unique_pmids <- function(references) {
  pmids <- unique(unlist(references))
  pmids <- pmids[!is.na(pmids) & pmids != "(reference needed)"]
  pmids
}

#' Fetch and Process All PubMed References
#'
#' Fetches publication info from PubMed for multiple PMIDs,
#' saves intermediate .bib and .xml files, and returns a
#' data.frame with formatted reference information.
#'
#' @param pmids A character vector of PMIDs.
#' @param bibentry_dir Directory to store .bib and .xml files.
#'   Defaults to "bibentry".
#' @param verbose If TRUE, prints progress messages. Defaults to FALSE.
#'
#' @return A data.frame with columns: pmid, formatted reference text.
#'
#' @export
fetch_all_pubmed_refs <- function(
  pmids,
  bibentry_dir = "bibentry",
  verbose = FALSE
) {
  # Ensure bibentry directory exists

  if (!dir.exists(bibentry_dir)) {
    dir.create(bibentry_dir, recursive = TRUE)
  }

  # Fetch and save bib/xml files
  for (i in seq_along(pmids)) {
    pmid <- pmids[i]

    if (verbose) {
      message(sprintf("Fetching PMID %d/%d: %s", i, length(pmids), pmid))
    }

    # Fetch publication info
    bib_to_write <- fetch_pub(pmid)

    # Save .bib file
    bib_file_name <- paste0(pmid, ".bib")
    RefManageR::WriteBib(
      bib_to_write,
      file = file.path(bibentry_dir, bib_file_name)
    )

    # Convert .bib file to .xml
    xml_file_name <- paste0(pmid, ".xml")
    rbibutils::bibConvert(
      infile = file.path(bibentry_dir, bib_file_name),
      outfile = file.path(bibentry_dir, xml_file_name)
    )
  }

  # Parse XML files and build result dataframe
  refs <- data.frame(
    pmid = pmids,
    stringsAsFactors = FALSE
  )

  for (i in seq_along(pmids)) {
    xml_path <- file.path(bibentry_dir, paste0(pmids[i], ".xml"))
    refs[i, 2L] <- parse_reference_xml(xml_path)
  }

  names(refs) <- c("pmid", "formatted_ref")

  # Manual correction of author name encoding issue
  refs$formatted_ref <- gsub(
    "H{\\a\u2f40?el{\\a\u2f40?ene",
    "H\u00e9l\u00e8ne",
    refs$formatted_ref,
    fixed = TRUE
  )

  refs
}
