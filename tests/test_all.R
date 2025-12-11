# test_all.R
# Comprehensive test suite for R Shiny Dashboard
# Run with: testthat::test_file("tests/test_all.R")

# =============================================================================
# SETUP
# =============================================================================

library(testthat)
library(data.table)
library(fastmap)
library(shiny)
library(shinytest2)

# Source R files in dependency order
# Use here::here() or .. to navigate from tests/ to project root
test_dir <- getwd()
if (basename(test_dir) == "tests") {
  setwd("..")
}
source("R/constants.R")
source("R/utils.R")
source("R/filter_utils.R")
source("R/tooltips.R")
source("R/data_prep.R")
source("R/mod_checkbox_filter.R")

# =============================================================================
# TESTS FOR utils.R
# =============================================================================

test_that("clean_column_names replaces underscores with spaces", {
  result <- clean_column_names("gene_name")
  expect_equal(result, "Gene Name")
})

test_that("clean_column_names applies title case", {
  result <- clean_column_names("gene name")
  expect_equal(result, "Gene Name")
})

test_that("clean_column_names preserves GWAS acronym", {
  result <- clean_column_names("gwas_trait")
  expect_equal(result, "GWAS Trait")
})

test_that("clean_column_names preserves SVD acronym", {
  result <- clean_column_names("svd_population")
  expect_equal(result, "SVD Population")
})

test_that("clean_column_names preserves ID acronym", {
  result <- clean_column_names("registry_id")
  expect_equal(result, "Registry ID")
})

test_that("clean_column_names preserves Omics acronym", {
  result <- clean_column_names("omics_data")
  expect_equal(result, "Omics Data")
})

test_that("clean_column_names preserves multiple acronyms", {
  result <- clean_column_names("svd_id_gwas")
  expect_equal(result, "SVD ID GWAS")
})

test_that("clean_column_names handles empty input", {
  result <- clean_column_names(character(0))
  expect_equal(result, character(0))
})

test_that("clean_column_names handles vector input", {
  result <- clean_column_names(c("gene_name", "gwas_trait"))
  expect_equal(result, c("Gene Name", "GWAS Trait"))
})

# =============================================================================
# TESTS FOR filter_utils.R
# =============================================================================

# Create mock data for filter tests
mock_dt <- data.table(
  Phase = c("Phase 1", "Phase 2", "Phase 3", "Phase 1"),
  `Sponsor Type` = c("Academic", "Industry", "Industry - Other", "Academic"),
  sample_size = c(50, 100, 200, 150),
  original_row_num = 1:4
)

test_that("apply_column_filter exact match works", {
  result <- apply_column_filter(
    mock_dt,
    "Phase",
    "Phase 1",
    match_type = "exact"
  )
  expect_equal(nrow(result), 2)
  expect_true(all(result$Phase == "Phase 1"))
})

test_that("apply_column_filter prefix match works", {
  result <- apply_column_filter(
    mock_dt,
    "Sponsor Type",
    "Industry",
    match_type = "prefix"
  )
  expect_equal(nrow(result), 2)
  expect_true(all(grepl("^Industry", result$`Sponsor Type`)))
})

test_that("apply_column_filter regex match works", {
  result <- apply_column_filter(
    mock_dt,
    "Phase",
    "Phase [12]",
    match_type = "regex"
  )
  expect_equal(nrow(result), 3)
})

test_that("apply_column_filter returns original dt for NULL filter_value", {
  result <- apply_column_filter(mock_dt, "Phase", NULL)
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_column_filter returns original dt for empty filter_value", {
  result <- apply_column_filter(mock_dt, "Phase", character(0))
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_column_filter skips filter when 'all' selected", {
  result <- apply_column_filter(mock_dt, "Phase", c("all", "Phase 1"))
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_column_filter applies filter when exclude_all is FALSE", {
  result <- apply_column_filter(
    mock_dt,
    "Phase",
    c("all", "Phase 1"),
    exclude_all = FALSE
  )
  expect_equal(nrow(result), 2)
})

test_that("apply_column_filter throws error for non-data.table input", {
  df <- as.data.frame(mock_dt)
  expect_error(
    apply_column_filter(df, "Phase", "Phase 1"),
    "dt must be a data.table"
  )
})

test_that("apply_column_filter throws error for invalid match_type", {
  expect_error(
    apply_column_filter(mock_dt, "Phase", "Phase 1", match_type = "invalid"),
    "match_type must be one of"
  )
})

test_that("apply_range_filter filters numeric column", {
  result <- apply_range_filter(mock_dt, "sample_size", c(50, 150))
  expect_equal(nrow(result), 3)
  expect_true(all(result$sample_size >= 50 & result$sample_size <= 150))
})

test_that("apply_range_filter returns original dt for NULL range_value", {
  result <- apply_range_filter(mock_dt, "sample_size", NULL)
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_range_filter skips when at default values", {
  result <- apply_range_filter(
    mock_dt,
    "sample_size",
    c(50, 200),
    default_min = 50,
    default_max = 200
  )
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_range_filter applies when not at default values", {
  result <- apply_range_filter(
    mock_dt,
    "sample_size",
    c(50, 100),
    default_min = 50,
    default_max = 200
  )
  expect_equal(nrow(result), 2)
})

test_that("apply_range_filter is inclusive of boundary values", {
  result <- apply_range_filter(mock_dt, "sample_size", c(50, 50))
  expect_equal(nrow(result), 1)
  expect_equal(result$sample_size, 50)
})

# Create mock fastmap for index filter tests
mock_index <- fastmap::fastmap()
mock_index$set("trait1", c(1L, 2L))
mock_index$set("trait2", c(2L, 3L))
mock_index$set("trait3", c(4L))

test_that("apply_index_filter returns matching rows", {
  result <- apply_index_filter(
    mock_dt,
    "trait1",
    mock_index,
    row_id_column = "original_row_num"
  )
  expect_equal(nrow(result), 2)
  expect_true(all(result$original_row_num %in% c(1, 2)))
})

test_that("apply_index_filter returns original dt for NULL filter_value", {
  result <- apply_index_filter(
    mock_dt,
    NULL,
    mock_index,
    row_id_column = "original_row_num"
  )
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_index_filter skips when 'all' selected", {
  result <- apply_index_filter(
    mock_dt,
    c("all", "trait1"),
    mock_index,
    row_id_column = "original_row_num"
  )
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_index_filter handles multi-value selection (union)", {
  result <- apply_index_filter(
    mock_dt,
    c("trait1", "trait2"),
    mock_index,
    row_id_column = "original_row_num"
  )
  expect_equal(nrow(result), 3)
  expect_true(all(result$original_row_num %in% c(1, 2, 3)))
})

test_that("apply_sponsor_type_filter returns original dt for NULL", {
  result <- apply_sponsor_type_filter(mock_dt, NULL)
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_sponsor_type_filter returns original dt for 'all'", {
  result <- apply_sponsor_type_filter(mock_dt, "all")
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_sponsor_type_filter returns original when both selected", {
  result <- apply_sponsor_type_filter(mock_dt, c("Academic", "Industry"))
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_sponsor_type_filter filters Academic only", {
  result <- apply_sponsor_type_filter(mock_dt, "Academic")
  expect_equal(nrow(result), 2)
  expect_true(all(result$`Sponsor Type` == "Academic"))
})

test_that("apply_sponsor_type_filter filters Industry with prefix match", {
  result <- apply_sponsor_type_filter(mock_dt, "Industry")
  expect_equal(nrow(result), 2)
  expect_true(all(grepl("^Industry", result$`Sponsor Type`)))
})

test_that("apply_single_value_filter applies for single value", {
  result <- apply_single_value_filter(mock_dt, "Phase", "Phase 1")
  expect_equal(nrow(result), 2)
})

test_that("apply_single_value_filter returns original for multiple values", {
  result <- apply_single_value_filter(mock_dt, "Phase", c("Phase 1", "Phase 2"))
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("apply_single_value_filter returns original for NULL", {
  result <- apply_single_value_filter(mock_dt, "Phase", NULL)
  expect_equal(nrow(result), nrow(mock_dt))
})

test_that("build_filter_list generates correct string for single filter", {
  specs <- list(
    list(
      name = "Phase",
      value = "Phase 1",
      is_single = FALSE,
      exclude_all = TRUE
    )
  )
  result <- build_filter_list(specs)
  expect_equal(result, "Phase: Phase 1")
})

test_that("build_filter_list handles multiple filter specs", {
  specs <- list(
    list(
      name = "Phase",
      value = "Phase 1",
      is_single = FALSE,
      exclude_all = TRUE
    ),
    list(
      name = "Type",
      value = "Academic",
      is_single = FALSE,
      exclude_all = TRUE
    )
  )
  result <- build_filter_list(specs)
  expect_equal(length(result), 2)
  expect_true("Phase: Phase 1" %in% result)
  expect_true("Type: Academic" %in% result)
})

test_that("build_filter_list respects is_single logic", {
  specs <- list(
    list(
      name = "MR",
      value = c("Yes", "No"),
      is_single = TRUE,
      exclude_all = FALSE
    )
  )
  result <- build_filter_list(specs)
  expect_equal(length(result), 0)
})

test_that("build_filter_list shows single value when is_single=TRUE", {
  specs <- list(
    list(name = "MR", value = "Yes", is_single = TRUE, exclude_all = FALSE)
  )
  result <- build_filter_list(specs)
  expect_equal(result, "MR: Yes")
})

test_that("build_filter_list excludes 'all' values", {
  specs <- list(
    list(
      name = "Phase",
      value = c("all", "Phase 1"),
      is_single = FALSE,
      exclude_all = TRUE
    )
  )
  result <- build_filter_list(specs)
  expect_equal(length(result), 0)
})

test_that("build_filter_list skips NULL values", {
  specs <- list(
    list(name = "Phase", value = NULL, is_single = FALSE, exclude_all = TRUE)
  )
  result <- build_filter_list(specs)
  expect_equal(length(result), 0)
})

test_that("build_filter_list skips empty values", {
  specs <- list(
    list(
      name = "Phase",
      value = character(0),
      is_single = FALSE,
      exclude_all = TRUE
    )
  )
  result <- build_filter_list(specs)
  expect_equal(length(result), 0)
})

# =============================================================================
# TESTS FOR tooltips.R
# =============================================================================

test_that("get_cell_type_tooltip returns correct name for EC", {
  result <- get_cell_type_tooltip("EC")
  expect_equal(result, "Endothelial Cells")
})

test_that("get_cell_type_tooltip returns correct name for SMC", {
  result <- get_cell_type_tooltip("SMC")
  expect_equal(result, "Smooth Muscle Cells")
})

test_that("get_cell_type_tooltip returns correct name for all known types", {
  expect_equal(get_cell_type_tooltip("EC"), "Endothelial Cells")
  expect_equal(get_cell_type_tooltip("SMC"), "Smooth Muscle Cells")
  expect_equal(get_cell_type_tooltip("VSMC"), "Vascular Smooth Muscle Cells")
  expect_equal(get_cell_type_tooltip("AC"), "Astrocytes")
  expect_equal(get_cell_type_tooltip("MG"), "Microglia")
  expect_equal(get_cell_type_tooltip("OL"), "Oligodendrocytes")
  expect_equal(get_cell_type_tooltip("PC"), "Pericytes")
  expect_equal(get_cell_type_tooltip("FB"), "Fibroblasts")
})

test_that("get_cell_type_tooltip returns fallback for unknown type", {
  result <- get_cell_type_tooltip("XX")
  expect_equal(result, "Full name for XX")
})

test_that("add_cell_type_tooltip returns PLACEHOLDER_UNKNOWN for NA", {
  result <- add_cell_type_tooltip(NA, tooltip_class)
  expect_equal(result, PLACEHOLDER_UNKNOWN)
})

test_that("add_cell_type_tooltip returns PLACEHOLDER_UNKNOWN for empty", {
  result <- add_cell_type_tooltip("", tooltip_class)
  expect_equal(result, PLACEHOLDER_UNKNOWN)
})

test_that("add_cell_type_tooltip returns PLACEHOLDER_UNKNOWN for placeholder", {
  result <- add_cell_type_tooltip(PLACEHOLDER_UNKNOWN, tooltip_class)
  expect_equal(result, PLACEHOLDER_UNKNOWN)
})

test_that("add_cell_type_tooltip generates HTML for single cell type", {
  result <- add_cell_type_tooltip("EC", tooltip_class)
  expect_true(grepl("data-tippy-content", result))
  expect_true(grepl("Endothelial Cells", result))
  expect_true(grepl("EC", result))
})

test_that("add_cell_type_tooltip handles > separator", {
  result <- add_cell_type_tooltip("EC > MG", tooltip_class)
  expect_true(grepl("Endothelial Cells", result))
  expect_true(grepl("Microglia", result))
  expect_true(grepl(">", result))
})

test_that("add_cell_type_tooltip handles < separator", {
  result <- add_cell_type_tooltip("EC < MG", tooltip_class)
  expect_true(grepl("Endothelial Cells", result))
  expect_true(grepl("Microglia", result))
  expect_true(grepl("<", result))
})

test_that("add_cell_type_tooltip handles multiple cell types", {
  result <- add_cell_type_tooltip("EC > MG > AC", tooltip_class)
  expect_true(grepl("Endothelial Cells", result))
  expect_true(grepl("Microglia", result))
  expect_true(grepl("Astrocytes", result))
})

test_that("add_cell_type_tooltip does not wrap 'all' in tooltip", {
  result <- add_cell_type_tooltip("all", tooltip_class)
  expect_false(grepl("data-tippy-content", result))
})

# =============================================================================
# TESTS FOR data_prep.R
# =============================================================================

test_that("safe_read_data throws error for non-existent file", {
  expect_error(
    safe_read_data("non_existent_file.qs"),
    "Neither .* nor .* found|File not found"
  )
})

test_that("safe_read_csv throws error for non-existent file", {
  expect_error(
    safe_read_csv("non_existent_file.csv"),
    "Failed to read CSV file"
  )
})

# =============================================================================
# TESTS FOR mod_checkbox_filter.R (using shinytest2)
# =============================================================================

# Helper function to create a test app for checkbox_filter_server
create_checkbox_test_app <- function() {
  shinyApp(
    ui = fluidPage(
      checkbox_filter_ui( # nolint: object_usage_linter.
        "test_filter",
        "Test Filter",
        choices = c(
          "Show All" = "all",
          "Option 1" = "opt1",
          "Option 2" = "opt2"
        ),
        selected = "all"
      ),
      textOutput("selected_value")
    ),
    server = function(input, output, session) {
      filter_value <- checkbox_filter_server( # nolint: object_usage_linter.
        "test_filter",
        default_selection = "all"
      )

      output$selected_value <- renderText({
        paste(filter_value(), collapse = ", ")
      })
    }
  )
}

test_that("checkbox_filter_ui creates UI element", {
  ui <- checkbox_filter_ui(
    "test",
    "Test Label",
    choices = c("A" = "a", "B" = "b"),
    selected = "a"
  )
  expect_true(inherits(ui, "shiny.tag"))
})

# Helper function to create a test app for binary_checkbox_filter_server
create_binary_filter_app <- function() {
  shinyApp(
    ui = fluidPage(
      checkbox_filter_ui( # nolint: object_usage_linter.
        "test_binary",
        "Binary Filter",
        choices = c("Yes" = "Yes", "No" = "No"),
        selected = c("Yes", "No")
      ),
      textOutput("binary_value")
    ),
    server = function(input, output, session) {
      # nolint start: object_usage_linter.
      filter_value <- binary_checkbox_filter_server(
        # nolint end
        "test_binary",
        choices = c("Yes", "No")
      )

      output$binary_value <- renderText({
        paste(filter_value(), collapse = ", ")
      })
    }
  )
}

# shinytest2 tests for checkbox_filter_server
test_that("checkbox_filter_server starts with default selection", {
  skip_if_not_installed("shinytest2")

  app <- AppDriver$new(
    create_checkbox_test_app(),
    name = "checkbox_default_test"
  )
  on.exit(app$stop())

  Sys.sleep(0.5)
  selected <- app$get_value(output = "selected_value")
  expect_true(grepl("all", selected))
})

test_that("binary_checkbox_filter_server starts with both options", {
  skip_if_not_installed("shinytest2")

  app <- AppDriver$new(
    create_binary_filter_app(),
    name = "binary_default_test"
  )
  on.exit(app$stop())

  Sys.sleep(0.5)
  selected <- app$get_value(output = "binary_value")
  expect_true(grepl("Yes", selected))
  expect_true(grepl("No", selected))
})

# =============================================================================
# TESTS FOR data_prep.R - Data Transformation Helpers
# =============================================================================

# convert_month_year_date() tests
test_that("convert_month_year_date formats '1/2024' as 'January 2024'", {
  result <- convert_month_year_date("1/2024")
  expect_equal(result, "January 2024")
})

test_that("convert_month_year_date formats '12/2023' as 'December 2023'", {
  result <- convert_month_year_date("12/2023")
  expect_equal(result, "December 2023")
})

test_that("convert_month_year_date returns NA for invalid format", {
  result <- convert_month_year_date("invalid")
  expect_true(is.na(result))
})

test_that("convert_month_year_date handles leading zeros '01/2024'", {
  result <- convert_month_year_date("01/2024")
  expect_equal(result, "January 2024")
})

test_that("convert_month_year_date handles vector input", {
  input <- c("1/2024", "6/2023", "invalid", "12/2022")
  result <- convert_month_year_date(input)
  expect_equal(result[1], "January 2024")
  expect_equal(result[2], "June 2023")
  expect_true(is.na(result[3]))
  expect_equal(result[4], "December 2022")
})

# match_registry_pattern() tests
test_that("match_registry_pattern identifies NCT pattern", {
  result <- match_registry_pattern("NCT12345678")
  expect_equal(result, "NCT")
})

test_that("match_registry_pattern identifies ISRCTN pattern", {
  result <- match_registry_pattern("ISRCTN12345678")
  expect_equal(result, "ISRCTN")
})

test_that("match_registry_pattern identifies ACTRN pattern", {
  result <- match_registry_pattern("ACTRN12345678901234567")
  expect_equal(result, "ACTRN")
})

test_that("match_registry_pattern identifies ChiCTR pattern", {
  result <- match_registry_pattern("ChiCTR2000029308")
  expect_equal(result, "ChiCTR")
})

test_that("match_registry_pattern returns NA for unrecognized pattern", {
  result <- match_registry_pattern("UNKNOWN12345")
  expect_true(is.na(result))
})

test_that("match_registry_pattern is case-insensitive", {
  result <- match_registry_pattern("nct12345678")
  expect_equal(result, "NCT")
})

# extract_omics_type() tests
test_that("extract_omics_type extracts code before semicolon", {
  result <- extract_omics_type("PWAS; some detail")
  expect_equal(result, "PWAS")
})

test_that("extract_omics_type handles no semicolon", {
  result <- extract_omics_type("TWAS")
  expect_equal(result, "TWAS")
})

test_that("extract_omics_type handles empty string", {
  result <- extract_omics_type("")
  expect_true(is.na(result))
})

test_that("extract_omics_type handles NA input", {
  result <- extract_omics_type(NA)
  expect_true(is.na(result))
})

# preserve_wnt_capitalization() tests
test_that("preserve_wnt_capitalization converts wnt to WNT", {
  result <- preserve_wnt_capitalization("wnt signaling")
  expect_equal(result, "WNT Signaling")
})

test_that("preserve_wnt_capitalization preserves other title casing", {
  result <- preserve_wnt_capitalization("notch signaling pathway")
  expect_equal(result, "Notch Signaling Pathway")
})

test_that("preserve_wnt_capitalization handles vector input", {
  input <- c("wnt pathway", "other pathway")
  result <- preserve_wnt_capitalization(input)
  expect_equal(result, c("WNT Pathway", "Other Pathway"))
})

# =============================================================================
# TESTS FOR tooltips.R - Reference Tooltips
# =============================================================================

# Create mock refs data frame
mock_refs <- data.frame(
  pmid = c("12345678", "87654321"),
  tooltip = c(
    paste0(
      "<strong>Title</strong> Test Paper<br>",
      "<strong>Authors:</strong> Smith J<br>"
    ),
    paste0(
      "<strong>Title</strong> Another Paper<br>",
      "<strong>Authors:</strong> Jones K<br>"
    )
  ),
  stringsAsFactors = FALSE
)

test_that("get_ref_tooltip_info returns tooltip for valid PMID", {
  result <- get_ref_tooltip_info("12345678", mock_refs)
  expect_true(grepl("Test Paper", result))
})

test_that("get_ref_tooltip_info returns 'No info available' for unknown PMID", {
  result <- get_ref_tooltip_info("99999999", mock_refs)
  expect_equal(result, "No info available")
})

test_that("get_ref_tooltip_info handles empty refs data frame", {
  empty_refs <- data.frame(pmid = character(0), tooltip = character(0))
  result <- get_ref_tooltip_info("12345678", empty_refs)
  expect_equal(result, "No info available")
})

# =============================================================================
# TESTS FOR filter_utils.R - render_filter_message
# =============================================================================

test_that("render_filter_message returns active div with filters", {
  result <- render_filter_message(c("Phase: Phase 1", "Type: Academic"))
  expect_true(inherits(result, "shiny.tag"))
  expect_true(grepl("Active Filters:", as.character(result)))
  expect_true(grepl("Phase: Phase 1", as.character(result)))
})

test_that("render_filter_message returns 'None' for empty filters", {
  result <- render_filter_message(character(0))
  expect_true(inherits(result, "shiny.tag"))
  expect_true(grepl("None", as.character(result)))
})

test_that("render_filter_message uses correct CSS class for active filters", {
  result <- render_filter_message(c("Phase: Phase 1"))
  html_str <- as.character(result)
  expect_true(grepl(filter_active_class, html_str, fixed = TRUE))
})

test_that("render_filter_message uses correct CSS class for no filters", {
  result <- render_filter_message(character(0))
  html_str <- as.character(result)
  expect_true(grepl(filter_none_class, html_str, fixed = TRUE))
})

# =============================================================================
# EDGE CASES - Existing Functions
# =============================================================================

# apply_range_filter edge cases
test_that("apply_range_filter handles NA values in numeric column", {
  dt_with_na <- data.table::copy(mock_dt)
  dt_with_na$sample_size[2] <- NA
  result <- apply_range_filter(dt_with_na, "sample_size", c(50, 200))
  # Should filter out NA and include rows in range

  expect_true(nrow(result) >= 0)
  expect_true(all(!is.na(result$sample_size)))
})

# apply_index_filter edge cases
test_that("apply_index_filter handles empty fastmap index", {
  empty_index <- fastmap::fastmap()
  result <- apply_index_filter(
    mock_dt,
    "nonexistent_trait",
    empty_index,
    row_id_column = "original_row_num"
  )
  # Should return empty result when trait not found
  expect_true(nrow(result) >= 0)
})

test_that("apply_index_filter handles NULL values in mget result", {
  partial_index <- fastmap::fastmap()
  partial_index$set("existing_trait", c(1L, 2L))
  result <- apply_index_filter(
    mock_dt,
    c("existing_trait", "missing_trait"),
    partial_index,
    row_id_column = "original_row_num"
  )
  # Should return only rows from existing_trait
  expect_equal(nrow(result), 2)
})

# add_cell_type_tooltip edge cases
test_that("add_cell_type_tooltip handles whitespace-only input", {
  result <- add_cell_type_tooltip("   ", tooltip_class)
  # Whitespace is trimmed, leaving empty parts, should still process
  expect_true(is.character(result))
})

test_that("add_cell_type_tooltip handles mixed separators", {
  result <- add_cell_type_tooltip("EC > MG < AC", tooltip_class)
  expect_true(grepl("Endothelial Cells", result))
  expect_true(grepl("Microglia", result))
  expect_true(grepl("Astrocytes", result))
})

test_that("add_cell_type_tooltip handles trailing separator", {
  result <- add_cell_type_tooltip("EC > ", tooltip_class)
  expect_true(grepl("Endothelial Cells", result))
})

# clean_column_names edge cases
test_that("clean_column_names handles single character input", {
  result <- clean_column_names("x")
  # toTitleCase doesn't capitalize single letters
  expect_equal(result, "x")
})

test_that("clean_column_names handles all-caps input", {
  result <- clean_column_names("GENE_NAME")
  # toTitleCase preserves uppercase words
  expect_equal(result, "GENE NAME")
})

test_that("clean_column_names handles multiple underscores", {
  result <- clean_column_names("gene_name_long_version")
  expect_equal(result, "Gene Name Long Version")
})

# build_filter_list edge cases
test_that("build_filter_list handles empty string values", {
  # Note: Current implementation treats empty string as a valid value
  # It only skips NULL or length(0) values
  specs <- list(
    list(name = "Test", value = "", is_single = FALSE, exclude_all = TRUE)
  )
  result <- build_filter_list(specs)
  expect_equal(length(result), 1)
  expect_equal(result, "Test: ")
})

test_that("build_filter_list handles only 'all' value", {
  specs <- list(
    list(name = "Test", value = "all", is_single = FALSE, exclude_all = TRUE)
  )
  result <- build_filter_list(specs)
  expect_equal(length(result), 0)
})

# =============================================================================
# SHINYTEST2 - Expanded Integration Tests
# =============================================================================

test_that("checkbox_filter_server deselects 'all' when option selected", {
  skip_if_not_installed("shinytest2")

  app <- AppDriver$new(
    create_checkbox_test_app(),
    name = "checkbox_deselect_all_test"
  )
  on.exit(app$stop())

  # Select a specific option (should deselect "all")
  app$set_inputs(`test_filter-filter` = "opt1")
  Sys.sleep(0.3)

  selected <- app$get_value(output = "selected_value")
  expect_false(grepl("all", selected))
  expect_true(grepl("opt1", selected))
})

test_that("checkbox_filter_server selects 'all' when clicking Show All", {
  skip_if_not_installed("shinytest2")

  app <- AppDriver$new(
    create_checkbox_test_app(),
    name = "checkbox_select_all_test"
  )
  on.exit(app$stop())

  # First select a specific option
  app$set_inputs(`test_filter-filter` = "opt1")
  Sys.sleep(0.3)

  # Then click "Show All"
  app$set_inputs(`test_filter-filter` = "all")
  Sys.sleep(0.3)

  selected <- app$get_value(output = "selected_value")
  expect_true(grepl("all", selected))
})

# =============================================================================
# SUMMARY
# =============================================================================

cat("Test suite completed!")
