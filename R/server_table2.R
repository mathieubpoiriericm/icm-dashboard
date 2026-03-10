# server_table2.R
# Server logic for Table 2 (Clinical Trials Table)
# nolint start: object_usage_linter.

# Build Table 2 Data Loader
#
# Creates a reactive that lazily loads Table 2 data only when needed.
# If PRELOAD_TABLE2 is TRUE, the data is preloaded at app startup and
# this loader simply returns the already-populated reactiveVals.
#
# Args:
#   table2_data: reactiveVal. Storage for table2 data.
#   table2_display_data: reactiveVal. Storage for display data.
#   ct_info_data: reactiveVal. Storage for clinical trial info.
#   registry_matches_data: reactiveVal. Storage for registry matches.
#   registry_rows_data: reactiveVal. Storage for registry row indices.
#   sample_sizes_data: reactiveVal. Storage for sample sizes.
#   sample_sizes_hash_data: reactiveVal. Storage for pre-computed hash.
#
# Returns:
#   A reactive that returns the loaded data list.
build_table2_loader <- function(
  table2_data,
  table2_display_data,
  ct_info_data,
  registry_matches_data,
  registry_rows_data,
  sample_sizes_data,
  sample_sizes_hash_data
) {
  shiny::reactive({
    # Load data if not already loaded (preloaded or lazy)
    if (is.null(table2_data())) {
      message("Loading Table 2 data...")

      data <- load_table2_data()

      table2_display <- prepare_table2_display(
        data$table2,
        data$ct_info,
        data$gene_info_table2,
        data$gene_symbols_table2,
        tooltip_class = tooltip_class,
        tooltip_class_italic = tooltip_class_italic
      )

      table2_data(data$table2)
      table2_display_data(table2_display)
      ct_info_data(data$ct_info)
      registry_matches_data(data$registry_matches)
      registry_rows_data(data$registry_rows)
      sample_sizes_data(data$sample_sizes)
      sample_sizes_hash_data(data$sample_sizes_hash)

      message("Table 2 data loaded successfully")
    }

    list(
      table2 = table2_data(),
      table2_display = table2_display_data(),
      ct_info = ct_info_data(),
      registry_matches = registry_matches_data(),
      registry_rows = registry_rows_data(),
      sample_sizes = sample_sizes_data(),
      sample_sizes_hash = sample_sizes_hash_data()
    )
  })
}

# Build Sample Size Histogram Output
#
# Creates the renderPlot expression for the sample size histogram
# with filter indicator lines.
#
# Args:
#   load_table2: Reactive. The table2 data loader.
#   sample_size_input: Reactive. The current slider input values.
#
# Returns:
#   A cached renderPlot expression.
build_sample_size_histogram <- function(load_table2, sample_size_input) {
  shiny::renderCachedPlot(
    {
      data <- load_table2()
      # Guard against NULL data during lazy loading
      shiny::req(data)
      sample_sizes <- data$sample_sizes

      par(mar = c(3L, 3L, 1L, 1L), bg = "white", family = "Raleway")
      hist(
        sample_sizes,
        breaks = HISTOGRAM_BREAKS,
        col = BRAND_COLOR_PRIMARY,
        border = "white",
        main = "",
        xlab = "",
        ylab = "",
        xlim = c(0L, HISTOGRAM_XLIM_MAX),
        las = 1L,
        xaxt = "n"
      )
      axis(
        1L,
        at = seq(0L, HISTOGRAM_XLIM_MAX, by = HISTOGRAM_TICK_INTERVAL),
        labels = FALSE
      )
      text(
        x = seq(0L, HISTOGRAM_XLIM_MAX, by = HISTOGRAM_TICK_INTERVAL),
        y = par("usr")[3L] - 0.5,
        labels = seq(0L, HISTOGRAM_XLIM_MAX, by = HISTOGRAM_TICK_INTERVAL),
        srt = 45L,
        adj = 1L,
        xpd = TRUE
      )

      if (!is.null(sample_size_input())) {
        abline(
          v = sample_size_input()[1L],
          col = BRAND_COLOR_DANGER,
          lwd = 2L,
          lty = 2L
        )
        abline(
          v = sample_size_input()[2L],
          col = BRAND_COLOR_DANGER,
          lwd = 2L,
          lty = 2L
        )
      }
    },
    cacheKeyExpr = {
      data <- load_table2()
      list(
        sample_sizes_hash = if (!is.null(data)) data$sample_sizes_hash else "",
        range = sample_size_input()
      )
    },
    sizePolicy = shiny::sizeGrowthRatio(
      width = 400, height = 180, growthRate = 1.2
    )
  )
}

# Build Table 2 Filtered Data Reactive
#
# Creates a reactive expression that filters table2 based on multiple
# filter selections including genetic evidence, registry, phase,
# population, sponsor, and sample size.
#
# Args:
#   load_table2: Reactive. The table2 data loader.
#   ge_filter: Reactive. Genetic evidence filter values.
#   reg_filter: Reactive. Registry filter values.
#   ct_filter: Reactive. Clinical trial phase filter values.
#   pop_filter: Reactive. SVD population filter values.
#   spon_filter: Reactive. Sponsor type filter values.
#   sample_size_filter_debounced: Reactive. Debounced sample size range.
#
# Returns:
#   A cached reactive expression returning filtered display data.
build_table2_filtered_data <- function(
  load_table2,
  ge_filter,
  reg_filter,
  ct_filter,
  pop_filter,
  spon_filter,
  sample_size_filter_debounced
) {
  shiny::reactive({
    data <- load_table2()
    # Guard against NULL data during lazy loading
    shiny::req(data, data$table2, data$registry_rows)
    table2 <- data$table2
    registry_rows <- data$registry_rows

    # Start with all row IDs, filter by intersection (avoids data.table copy)
    kept_rows <- table2$original_row_num

    # Apply filters by computing row intersections
    # Genetic Evidence filter
    if (!is.null(ge_filter()) && length(ge_filter()) == 1L) {
      ge_rows <- table2[
        get("Genetic Evidence") %in% ge_filter(), original_row_num
      ]
      kept_rows <- intersect(kept_rows, ge_rows)
    }

    # Registry filter (using fastmap for O(1) lookups)
    if (
      !is.null(reg_filter()) &&
        length(reg_filter()) > 0L &&
        !"all" %in% reg_filter()
    ) {
      reg_rows <- unique(unlist(registry_rows$mget(reg_filter())))
      kept_rows <- intersect(kept_rows, reg_rows)
    }

    # Clinical Trial Phase filter
    if (
      !is.null(ct_filter()) &&
        length(ct_filter()) > 0L &&
        !"all" %in% ct_filter()
    ) {
      ct_rows <- table2[
        get("Clinical Trial Phase") %in% ct_filter(),
        original_row_num
      ]
      kept_rows <- intersect(kept_rows, ct_rows)
    }

    # SVD Population filter
    if (
      !is.null(pop_filter()) &&
        length(pop_filter()) > 0L &&
        !"all" %in% pop_filter()
    ) {
      pop_rows <- table2[
        get("SVD Population") %in% pop_filter(), original_row_num
      ]
      kept_rows <- intersect(kept_rows, pop_rows)
    }

    # Sponsor Type filter (special logic for Academic/Industry)
    if (
      !is.null(spon_filter()) &&
        length(spon_filter()) > 0L &&
        !"all" %in% spon_filter()
    ) {
      if (!("Academic" %in% spon_filter() && "Industry" %in% spon_filter())) {
        if ("Academic" %in% spon_filter()) {
          spon_rows <- table2[
            get("Sponsor Type") == "Academic",
            original_row_num
          ]
        } else {
          spon_rows <- table2[
            grepl("^Industry", get("Sponsor Type")),
            original_row_num
          ]
        }
        kept_rows <- intersect(kept_rows, spon_rows)
      }
    }

    # Sample size range filter
    if (!is.null(sample_size_filter_debounced())) {
      range_val <- sample_size_filter_debounced()
      if (
        range_val[1L] != SAMPLE_SIZE_MIN || range_val[2L] != SAMPLE_SIZE_MAX
      ) {
        size_rows <- table2[
          sample_size_numeric >= range_val[1L] &
            sample_size_numeric <= range_val[2L],
          original_row_num
        ]
        kept_rows <- intersect(kept_rows, size_rows)
      }
    }

    # Get filtered display data, sorted by Drug for proper row merging
    filtered_display <- data$table2_display[kept_rows, ]
    filtered_display <- filtered_display[order(filtered_display$Drug), ]
    rownames(filtered_display) <- NULL

    # Pre-compute drug group indices for JavaScript row merging
    filtered_display <- add_drug_group_indices(filtered_display)

    filtered_display
  }) |>
    shiny::bindCache(
      ge_filter(),
      reg_filter(),
      ct_filter(),
      pop_filter(),
      spon_filter(),
      sample_size_filter_debounced()
    )
}

# Add Row Merge Metadata for JavaScript
#
# Pre-computes rowspan values, visibility, and color assignments for
# JavaScript row merging. This moves computation from client-side JS
# (which runs on every page draw) to server-side R (which runs once
# when data is filtered).
#
# Args:
#   display_df: Data frame. The display data.
#
# Returns:
#   Data frame with hidden metadata columns added:
#   - __drug_group__: Drug group index (0-indexed)
#   - __color_class__: Alternating color class (0 or 1)
#   - __rowspans__: JSON array of rowspan values for columns 0-3
add_drug_group_indices <- function(display_df) {
  n_rows <- nrow(display_df)

  if (n_rows == 0L) {
    display_df$`__drug_group__` <- integer(0L)
    display_df$`__color_class__` <- integer(0L)
    display_df$`__rowspans__` <- character(0L)
    return(display_df)
  }

  # Strip HTML helper for text comparison
  strip_html <- function(x) gsub("<[^>]+>", "", x)

  # Columns to merge (0-indexed): Drug, Genetic Target, Trial Phase, Population
  merge_cols <- c("Drug", "Genetic Target", "Clinical Trial Phase",
                  "SVD Population")

  # Extract text values for comparison (strip HTML)
  col_values <- lapply(merge_cols, function(col) {
    if (col %in% names(display_df)) strip_html(display_df[[col]])
    else rep("", n_rows)
  })

  # Compute drug groups (vectorized using cumsum)
  drugs <- col_values[[1L]]
  drug_group_idx <- cumsum(c(TRUE, drugs[-1L] != drugs[-n_rows]))

  # Compute color classes (alternating by drug group)
  # Group 1 = class 0, Group 2 = class 1, Group 3 = class 0, etc.
  color_class <- (drug_group_idx - 1L) %% 2L

  # Compute rowspans for each column
  # For each row, rowspan = 0 means hidden, rowspan > 0 means visible with span
  rowspans_matrix <- matrix(0L, nrow = n_rows, ncol = length(merge_cols))

  for (col_idx in seq_along(merge_cols)) {
    vals <- col_values[[col_idx]]
    i <- 1L
    while (i <= n_rows) {
      # Find span of identical values within same drug group
      span_start <- i
      current_val <- vals[i]
      current_drug_group <- drug_group_idx[i]

      while (i < n_rows &&
               vals[i + 1L] == current_val &&
               current_val != "" &&
               drug_group_idx[i + 1L] == current_drug_group) {
        i <- i + 1L
      }

      # Set rowspan for first row of group, 0 for others (hidden)
      rowspans_matrix[span_start, col_idx] <- i - span_start + 1L
      # Rows span_start+1 to i remain 0 (hidden)

      i <- i + 1L
    }
  }

  # Convert rowspans to JSON strings (vectorized paste instead of per-row toJSON)
  rowspans_json <- paste0(
    "[", rowspans_matrix[, 1L], ",", rowspans_matrix[, 2L], ",",
    rowspans_matrix[, 3L], ",", rowspans_matrix[, 4L], "]"
  )

  # Add hidden columns
  display_df$`__drug_group__` <- drug_group_idx - 1L  # 0-indexed
  display_df$`__color_class__` <- color_class
  display_df$`__rowspans__` <- rowspans_json

  display_df
}

# Build Table 2 Filter Message UI
#
# Creates a reactive UI expression that displays active filter status
# for Table 2.
#
# Args:
#   ge_filter: Reactive. Genetic evidence filter values.
#   reg_filter: Reactive. Registry filter values.
#   ct_filter: Reactive. Clinical trial phase filter values.
#   pop_filter: Reactive. SVD population filter values.
#   spon_filter: Reactive. Sponsor type filter values.
#   sample_size_filter_debounced: Reactive. Debounced sample size range.
#
# Returns:
#   A cached renderUI expression.
build_table2_filter_message <- function(
  ge_filter,
  reg_filter,
  ct_filter,
  pop_filter,
  spon_filter,
  sample_size_filter_debounced
) {
  shiny::renderUI({
    filters_applied <- build_filter_list(
      list(
        list(
          name = "Genetic Evidence",
          value = ge_filter(),
          is_single = TRUE
        ),
        list(
          name = "Clinical Trial Registry",
          value = reg_filter(),
          exclude_all = TRUE
        ),
        list(
          name = "Clinical Trial Phase",
          value = ct_filter(),
          exclude_all = TRUE
        ),
        list(
          name = "Population",
          value = pop_filter(),
          exclude_all = TRUE
        ),
        list(
          name = "Sponsor",
          value = spon_filter(),
          exclude_all = TRUE
        )
      )
    )

    # Add sample size filter if not at default values
    if (!is.null(sample_size_filter_debounced())) {
      if (
        sample_size_filter_debounced()[1L] != SAMPLE_SIZE_MIN ||
          sample_size_filter_debounced()[2L] != SAMPLE_SIZE_MAX
      ) {
        filters_applied <- c(
          filters_applied,
          paste0(
            "Sample Size: ",
            sample_size_filter_debounced()[1L],
            " - ",
            sample_size_filter_debounced()[2L]
          )
        )
      }
    }

    render_filter_message(filters_applied)
  }) |>
    shiny::bindCache(
      ge_filter(),
      reg_filter(),
      ct_filter(),
      pop_filter(),
      spon_filter(),
      sample_size_filter_debounced()
    )
}

# Build Table 2 DataTable Output
#
# Creates the DT::renderDT expression for Table 2 with row merging.
# Row merging metadata (rowspans, colors) is pre-computed server-side
# in add_drug_group_indices() to minimize JavaScript computation on
# each page draw.
#
# Args:
#   filtered_data2: Reactive. The filtered display data.
#
# Returns:
#   A DT::renderDT expression.
build_table2_datatable <- function(filtered_data2) {
  DT::renderDT(
    {
      display_data <- filtered_data2()
      # Validate data is available and not empty
      shiny::validate(
        shiny::need(
          !is.null(display_data) && nrow(display_data) > 0L,
          paste(
            "No clinical trials match the selected filters.",
            "Try adjusting your filter criteria."
          )
        )
      )

      # Get indices of hidden metadata columns (0-indexed for JS)
      col_names <- names(display_data)
      drug_group_col_idx <- which(col_names == "__drug_group__") - 1L
      color_class_col_idx <- which(col_names == "__color_class__") - 1L
      rowspans_col_idx <- which(col_names == "__rowspans__") - 1L

      dt2 <- DT::datatable(
        display_data,
        escape = FALSE,
        rownames = FALSE,
        plugins = "natural",
        options = list(
          columnDefs = list(
            # Hide all metadata columns but keep data accessible
            list(
              visible = FALSE,
              targets = c(drug_group_col_idx, color_class_col_idx,
                          rowspans_col_idx)
            )
          ),
          autoWidth = TRUE,
          pageLength = DATATABLE_PAGE_LENGTH,
          deferRender = TRUE,
          dom = "rtip",
          scrollX = TRUE,
          scrollCollapse = TRUE,
          searchDelay = DATATABLE_SEARCH_DELAY,
          initComplete = DT::JS(
            "function() {
              $(this.api().table().header())
                .find('th').css('text-align', 'center');

              // Cache stripe colors at init (runs once, not on every draw)
              var rows = this.api().rows().nodes();
              if (rows.length >= 2) {
                this._stripeColor = $(rows[1]).css('background-color');
                this._whiteColor = $(rows[0]).css('background-color');
              }
            }"
          ),
          drawCallback = DT::JS(sprintf(
            "function(settings) {
              // Initialize tooltips
              if (typeof initializeTippy === 'function') {
                initializeTippy();
              }

              var api = this.api();
              var rows = api.rows({page: 'current'}).nodes();
              var numRows = rows.length;

              if (numRows === 0) return;

              // Columns to merge (Drug, Target, Phase, Population)
              var columnsToMerge = [0, 2, 5, 6];

              // Reset all mergeable cells to default state first
              for (var i = 0; i < numRows; i++) {
                var rowCells = $(rows[i]).children('td');
                for (var c = 0; c < columnsToMerge.length; c++) {
                  var cell = rowCells.eq(columnsToMerge[c]);
                  cell.css('display', '');
                  cell.removeAttr('rowspan');
                  cell.css('vertical-align', '');
                  cell.css('background-color', '');
                }
              }

              // Check if sorted by Drug column (column 0)
              // Only apply row merging when sorted by Drug
              var order = api.order();
              var sortedByDrug = order.length === 0 ||
                (order[0][0] === 0 && order[0][1] === 'asc');

              if (!sortedByDrug) {
                return;
              }

              if (numRows < 2) {
                return;
              }

              // Get pre-computed metadata from hidden columns
              var colorClasses = api
                .column(%d, {page: 'current'}).data().toArray();
              var rowspansData = api
                .column(%d, {page: 'current'}).data().toArray();

              // Use cached colors from initComplete, fallback if needed
              var stripeColor = this._stripeColor ||
                $(rows[0]).css('background-color');
              var whiteColor = this._whiteColor ||
                $(rows[1]).css('background-color');
              var colors = [whiteColor, stripeColor];

              // Single pass: apply pre-computed colors and rowspans
              for (var i = 0; i < numRows; i++) {
                var rowCells = $(rows[i]).children('td');
                var bgColor = colors[colorClasses[i]];
                var rowspans = JSON.parse(rowspansData[i]);

                for (var c = 0; c < columnsToMerge.length; c++) {
                  var cell = rowCells.eq(columnsToMerge[c]);
                  cell.css('background-color', bgColor);

                  // Clamp to remaining rows on page (handles groups spanning page boundaries)
                  var span = Math.min(rowspans[c], numRows - i);
                  if (span === 0) {
                    // Hidden cell (merged into previous)
                    cell.css('display', 'none');
                  } else if (span > 1) {
                    // First cell of merged group
                    cell.attr('rowspan', span);
                    cell.css('vertical-align', 'middle');
                  } else {
                    // Normal cell (span === 1)
                    cell.css('vertical-align', 'middle');
                  }
                }
              }
            }",
            color_class_col_idx,
            rowspans_col_idx
          ))
        )
      ) |>
        DT::formatStyle(columns = c("Genetic Target"), fontStyle = "italic")

      dt2
    },
    server = DATATABLE_SERVER_SIDE
  )
}
# nolint end: object_usage_linter.
