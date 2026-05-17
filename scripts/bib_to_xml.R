# bib_to_xml.R
# Convert .bib files in data/bibentry/bib_extra to MODS .xml in
# data/bibentry/xml. Uses bibutils' `bib2xml` so the output matches
# the format already in data/bibentry/xml/. Originals are left
# untouched.
#
# Usage:
#   Rscript scripts/bib_to_xml.R
#   Rscript scripts/bib_to_xml.R <input_dir> <output_dir>

# =============================================================================
# CONFIG
# =============================================================================

INPUT_DIR_DEFAULT <- "data/bibentry/bib_extra"
OUTPUT_DIR_DEFAULT <- "data/bibentry/xml"
BIB2XML <- "bib2xml"

# =============================================================================
# HELPERS
# =============================================================================

ensure_bib2xml <- function() {
  if (!nzchar(Sys.which(BIB2XML))) {
    stop(
      "Required tool '", BIB2XML, "' was not found on PATH.\n",
      "Install bibutils (macOS): brew install bibutils\n",
      "Then re-run this script.",
      call. = FALSE
    )
  }
}

convert_one <- function(bib_path, xml_path) {
  tmp_path <- paste0(xml_path, ".part")
  err_path <- paste0(xml_path, ".err")
  on.exit(
    {
      if (file.exists(tmp_path)) unlink(tmp_path)
      if (file.exists(err_path)) unlink(err_path)
    },
    add = TRUE
  )

  status <- suppressWarnings(system2(
    BIB2XML,
    args = shQuote(bib_path),
    stdout = tmp_path,
    stderr = err_path
  ))

  if (!identical(as.integer(status), 0L)) {
    err_msg <- if (file.exists(err_path)) {
      paste(readLines(err_path, warn = FALSE), collapse = " | ")
    } else {
      sprintf("bib2xml exited with status %s", as.character(status))
    }
    return(list(ok = FALSE, message = err_msg))
  }

  if (!file.rename(tmp_path, xml_path)) {
    return(list(ok = FALSE, message = "failed to move output into place"))
  }
  list(ok = TRUE, message = "")
}

# =============================================================================
# MAIN
# =============================================================================

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  input_dir <- if (length(args) >= 1L) args[[1L]] else INPUT_DIR_DEFAULT
  output_dir <- if (length(args) >= 2L) args[[2L]] else OUTPUT_DIR_DEFAULT

  # Match the convention used by scripts/trigger_update.R so the script
  # works whether invoked from the project root or from scripts/.
  if (grepl("scripts$", getwd())) {
    setwd("..")
  }

  ensure_bib2xml()

  if (!dir.exists(input_dir)) {
    stop("Input directory does not exist: ", input_dir, call. = FALSE)
  }

  if (!dir.exists(output_dir)) {
    dir.create(output_dir, recursive = TRUE)
    message("Created output directory: ", output_dir)
  }

  bib_files <- list.files(input_dir, pattern = "\\.bib$", full.names = TRUE)

  if (length(bib_files) == 0L) {
    message("No .bib files found in: ", input_dir)
    return(invisible(NULL))
  }

  message(sprintf(
    "Converting %d .bib file(s) from %s -> %s ...",
    length(bib_files), input_dir, output_dir
  ))

  ok_count <- 0L
  skip_count <- 0L
  fail_count <- 0L
  for (bib_path in bib_files) {
    stem <- tools::file_path_sans_ext(basename(bib_path))
    xml_path <- file.path(output_dir, paste0(stem, ".xml"))
    # Skip if an XML for this PMID already exists — bib_extra is for
    # *new* entries, and BibTeX-derived MODS lacks the abstract that
    # PDF-derived entries already in xml/ carry, so a blind overwrite
    # would silently lose content.
    if (file.exists(xml_path)) {
      skip_count <- skip_count + 1L
      message(sprintf(
        "  skip %s: %s already exists (remove it first to re-convert)",
        basename(bib_path), basename(xml_path)
      ))
      next
    }
    res <- convert_one(bib_path, xml_path)
    if (res$ok) {
      ok_count <- ok_count + 1L
      message(sprintf("  ok   %s -> %s", basename(bib_path), basename(xml_path)))
    } else {
      fail_count <- fail_count + 1L
      message(sprintf("  FAIL %s: %s", basename(bib_path), res$message))
    }
  }

  message(sprintf(
    "\nDone: %d succeeded, %d skipped, %d failed.",
    ok_count, skip_count, fail_count
  ))
  if (fail_count > 0L) quit(status = 1L)
}

main()
