# fetch_pubmed_data.R
# PubMed reference fetching and cleaning functions

# Fetch Publication from PubMed
#
# Fetches publication info from PubMed for a single PMID.
#
# Args:
#   pmid: A character or numeric PMID.
#
# Returns:
#   A BibEntry object from RefManageR.
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

  ref <- NULL

  if (ncbi_available) {
    ref <- tryCatch(
      RefManageR::ReadPubMed(query = as.character(pmid)),
      error = function(e) {
        warning(sprintf("Failed to fetch PMID %s: %s", pmid, e$message))
        NULL
      }
    )
  } else {
    warning("NCBI is not reachable, skipping PubMed fetch")
  }

  ref
}

# Parse XML Reference File
#
# Parses a MODS XML file and extracts formatted reference information.
#
# Args:
#   xml_path: Path to the XML file.
#
# Returns:
#   A character string with HTML-formatted reference info.
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

# Extract Unique PMIDs from Table1
#
# Extracts and cleans unique PMIDs from a table1 References column.
#
# Args:
#   references: A list column of reference PMIDs.
#
# Returns:
#   A character vector of unique PMIDs.
extract_unique_pmids <- function(references) {
  pmids <- unique(unlist(references))
  pmids <- pmids[!is.na(pmids) & pmids != "(reference needed)"]
  pmids
}

# Fetch and Process All PubMed References
#
# Fetches publication info from PubMed for multiple PMIDs,
# saves intermediate .bib and .xml files, and returns a
# data.frame with formatted reference information.
#
# Args:
#   pmids: A character vector of PMIDs.
#   bibentry_dir: Directory to store .bib and .xml files.
#     Defaults to "bibentry".
#   delay: Delay in seconds between API requests to avoid rate limiting.
#     NCBI recommends no more than 3 requests per second. Defaults to 0.5.
#   verbose: If TRUE, prints progress messages. Defaults to FALSE.
#
# Returns:
#   A data.frame with columns: pmid, formatted reference text.
fetch_all_pubmed_refs <- function(
  pmids,
  bibentry_dir = "bibentry",
  delay = 0.5,
  verbose = FALSE
) {
  # Ensure bibentry directory exists
  if (!dir.exists(bibentry_dir)) {
    dir.create(bibentry_dir, recursive = TRUE)
  }

  # Track which PMIDs were successfully fetched
  successful_pmids <- character(0)

  # Fetch and save bib/xml files
  for (i in seq_along(pmids)) {
    pmid <- pmids[i]

    if (verbose) {
      message(sprintf("Fetching PMID %d/%d: %s", i, length(pmids), pmid))
    }

    # Fetch publication info
    bib_to_write <- fetch_pub(pmid)

    # Skip if fetch failed
    if (is.null(bib_to_write) || length(bib_to_write) == 0) {
      if (verbose) {
        message(sprintf("  Skipping PMID %s - fetch returned NULL", pmid))
      }
      next
    }

    # Save .bib file
    bib_file_name <- paste0(pmid, ".bib")
    tryCatch(
      {
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

        successful_pmids <- c(successful_pmids, pmid)
      },
      error = function(e) {
        if (verbose) {
          message(sprintf("  Error processing PMID %s: %s", pmid, e$message))
        }
      }
    )

    # Delay between requests to avoid rate limiting
    if (i < length(pmids)) {
      Sys.sleep(delay)
    }
  }

  if (length(successful_pmids) == 0) {
    warning("No PMIDs were successfully fetched")
    return(data.frame(
      pmid = character(0),
      formatted_ref = character(0),
      stringsAsFactors = FALSE
    ))
  }

  # Parse XML files and build result dataframe
  refs <- data.frame(
    pmid = successful_pmids,
    formatted_ref = NA_character_,
    stringsAsFactors = FALSE
  )

  for (i in seq_along(successful_pmids)) {
    xml_path <- file.path(bibentry_dir, paste0(successful_pmids[i], ".xml"))
    refs$formatted_ref[i] <- tryCatch(
      parse_reference_xml(xml_path),
      error = function(e) {
        if (verbose) {
          message(sprintf("  Error parsing XML for PMID %s: %s",
                          successful_pmids[i], e$message))
        }
        NA_character_
      }
    )
  }

  # Manual correction of author name encoding issue
  refs$formatted_ref <- gsub(
    "H{\\a\u2f40?el{\\a\u2f40?ene",
    "H\u00e9l\u00e8ne",
    refs$formatted_ref,
    fixed = TRUE
  )

  refs
}
