# server_table2.R
# Server logic for Table 2 (Clinical Trials Table)
# nolint start: object_usage_linter.

#' Build Table 2 Data Loader
#'
#' Creates a reactive that lazily loads Table 2 data only when needed.
#' Supports async loading with promises/future when ASYNC_TABLE2_LOADING
#' is TRUE.
#'
#' @param table2_data reactiveVal. Storage for table2 data.
#' @param table2_display_data reactiveVal. Storage for display data.
#' @param ct_info_data reactiveVal. Storage for clinical trial info.
#' @param registry_matches_data reactiveVal. Storage for registry matches.
#' @param registry_rows_data reactiveVal. Storage for registry row indices.
#' @param sample_sizes_data reactiveVal. Storage for sample sizes.
#' @param sample_sizes_hash_data reactiveVal. Storage for pre-computed hash.
#'
#' @return A reactive that returns the loaded data list.
#'
#' @keywords internal
build_table2_loader <- function(
  table2_data,
  table2_display_data,
  ct_info_data,
  registry_matches_data,
  registry_rows_data,
  sample_sizes_data,
  sample_sizes_hash_data
) {
  # Track loading state for async operations
  loading_in_progress <- shiny::reactiveVal(FALSE)

  shiny::reactive({
    if (is.null(table2_data()) && !loading_in_progress()) {
      message("Loading Table 2 data...")

      # Check if async loading is enabled and packages are available
      use_async <- ASYNC_TABLE2_LOADING &&
        exists("ASYNC_PACKAGES_AVAILABLE") &&
        ASYNC_PACKAGES_AVAILABLE

      if (use_async) {
        # Async loading with future/promises
        loading_in_progress(TRUE)
        future::future({
          load_table2_data()
        }, seed = TRUE) |>
          promises::then(
            onFulfilled = function(data) {
              table2_display <- prepare_table2_display(
                data$table2,
                data$ct_info,
                data$gene_info_table2,
                data$gene_symbols_table2
              )
              table2_data(data$table2)
              table2_display_data(table2_display)
              ct_info_data(data$ct_info)
              registry_matches_data(data$registry_matches)
              registry_rows_data(data$registry_rows)
              sample_sizes_data(data$sample_sizes)
              sample_sizes_hash_data(data$sample_sizes_hash)
              loading_in_progress(FALSE)
              message("Table 2 data loaded successfully (async)")
            },
            onRejected = function(e) {
              loading_in_progress(FALSE)
              warning("Async Table 2 loading failed: ", e$message)
            }
          )
        # Return NULL while loading
        return(NULL)
      } else {
        # Synchronous loading (default)
        data <- load_table2_data()

        table2_display <- prepare_table2_display(
          data$table2,
          data$ct_info,
          data$gene_info_table2,
          data$gene_symbols_table2
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
    }

    # Return NULL if still loading or not yet loaded
    if (is.null(table2_data())) {
      return(NULL)
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

#' Build Sample Size Histogram Output
#'
#' Creates the renderPlot expression for the sample size histogram
#' with filter indicator lines.
#'
#' @param load_table2 Reactive. The table2 data loader.
#' @param sample_size_input Reactive. The current slider input values.
#'
#' @return A cached renderPlot expression.
#'
#' @keywords internal
build_sample_size_histogram <- function(load_table2, sample_size_input) {
  shiny::renderPlot({
    data <- load_table2()
    sample_sizes <- data$sample_sizes

    par(mar = c(3L, 3L, 1L, 1L), bg = "white", family = "Roboto")
    hist(
      sample_sizes,
      breaks = HISTOGRAM_BREAKS,
      col = "#2d287a",
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
        col = "#e52f12",
        lwd = 2L,
        lty = 2L
      )
      abline(
        v = sample_size_input()[2L],
        col = "#e52f12",
        lwd = 2L,
        lty = 2L
      )
    }
  }) |>
    shiny::bindCache(
      load_table2()$sample_sizes_hash,
      sample_size_input()
    )
}

#' Build Table 2 Filtered Data Reactive
#'
#' Creates a reactive expression that filters table2 based on multiple
#' filter selections including genetic evidence, registry, phase,
#' population, sponsor, and sample size.
#'
#' @param load_table2 Reactive. The table2 data loader.
#' @param ge_filter Reactive. Genetic evidence filter values.
#' @param reg_filter Reactive. Registry filter values.
#' @param ct_filter Reactive. Clinical trial phase filter values.
#' @param pop_filter Reactive. SVD population filter values.
#' @param spon_filter Reactive. Sponsor type filter values.
#' @param sample_size_filter_debounced Reactive. Debounced sample size range.
#'
#' @return A cached reactive expression returning filtered display data.
#'
#' @keywords internal
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

#' Add Drug Group Indices for Row Merging
#'
#' Pre-computes drug group indices for JavaScript row merging,
#' avoiding recalculation on each page draw.
#'
#' @param display_df Data frame. The display data.
#'
#' @return Data frame with __drug_group__ column added.
#'
#' @keywords internal
add_drug_group_indices <- function(display_df) {
  drugs <- display_df$Drug
  drug_group_idx <- integer(length(drugs))

  if (length(drugs) > 0L) {
    current_group <- 0L
    last_drug <- ""
    for (i in seq_along(drugs)) {
      drug_text <- gsub("<[^>]+>", "", drugs[i]) # Strip HTML for comparison
      if (drug_text != last_drug) {
        current_group <- current_group + 1L
        last_drug <- drug_text
      }
      drug_group_idx[i] <- current_group
    }
  }

  # Add hidden column with drug group index (0-indexed for JavaScript)
  display_df$`__drug_group__` <- drug_group_idx - 1L

  display_df
}

#' Build Table 2 Filter Message UI
#'
#' Creates a reactive UI expression that displays active filter status
#' for Table 2.
#'
#' @param ge_filter Reactive. Genetic evidence filter values.
#' @param reg_filter Reactive. Registry filter values.
#' @param ct_filter Reactive. Clinical trial phase filter values.
#' @param pop_filter Reactive. SVD population filter values.
#' @param spon_filter Reactive. Sponsor type filter values.
#' @param sample_size_filter_debounced Reactive. Debounced sample size range.
#'
#' @return A cached renderUI expression.
#'
#' @keywords internal
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

#' Build Table 2 DataTable Output
#'
#' Creates the DT::renderDT expression for Table 2 with row merging
#' JavaScript for drug grouping.
#'
#' @param filtered_data2 Reactive. The filtered display data.
#'
#' @return A DT::renderDT expression.
#'
#' @keywords internal
build_table2_datatable <- function(filtered_data2) {
  DT::renderDT(
    {
      display_data <- filtered_data2()
      # Get the index of the hidden drug group column
      drug_group_col_idx <- which(
        names(display_data) == "__drug_group__"
      ) -
        1L

      dt2 <- DT::datatable(
        display_data,
        escape = FALSE,
        rownames = FALSE,
        plugins = "natural",
        options = list(
          columnDefs = list(
            # Hide the drug group column but keep data accessible
            list(visible = FALSE, targets = drug_group_col_idx)
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

              // Reset all mergeable cells to default state first
              var columnsToMerge = [0, 1, 2, 3];
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
              var sortedByDrug = order.length === 0 || order[0][0] === 0;

              if (!sortedByDrug) {
                // Not sorted by Drug - skip row merging
                return;
              }

              // Skip custom coloring for single row (striping is meaningless)
              if (numRows < 2) {
                return;
              }

              // Get pre-computed drug group indices (single API call)
              var drugGroupIndex = api
                .column(%d, {page: 'current'})
                .data().toArray();

              // Get colors from first two rows (cache these)
              var stripeColor = $(rows[0]).css('background-color');
              var whiteColor = $(rows[1]).css('background-color');

              // Single pass: compute group boundaries and colors
              var groupInfo = {};
              var sortedGroups = [];
              var i, g;

              for (i = 0; i < numRows; i++) {
                g = drugGroupIndex[i];
                if (groupInfo[g] === undefined) {
                  groupInfo[g] = {first: i, last: i, count: 1};
                  sortedGroups.push(g);
                } else {
                  groupInfo[g].last = i;
                  groupInfo[g].count++;
                }
              }

              // Compute colors for each group
              var drugGroupColors = {};
              for (i = 0; i < sortedGroups.length; i++) {
                g = sortedGroups[i];
                if (i === 0) {
                  drugGroupColors[g] = whiteColor;
                } else {
                  var prevG = sortedGroups[i - 1];
                  var prevInfo = groupInfo[prevG];
                  var prevRowIsStripe = (prevInfo.last %% 2 === 0);
                  var prevHasEvenRows = (prevInfo.count %% 2 === 0);
                  drugGroupColors[g] = (prevHasEvenRows ? prevRowIsStripe :
                    !prevRowIsStripe) ? stripeColor : whiteColor;
                }
              }

              // Cache column nodes for merging
              var colNodes = columnsToMerge.map(function(idx) {
                return api.column(idx, {page: 'current'}).nodes();
              });

              // Single pass: apply colors and compute rowspans
              var mergeState = columnsToMerge.map(function() {
                return {lastVal: null, lastNode: null,
                  lastGroup: null, span: 1};
              });

              for (i = 0; i < numRows; i++) {
                var rowCells = $(rows[i]).children('td');
                var bgColor = drugGroupColors[drugGroupIndex[i]];
                var currentGroup = drugGroupIndex[i];

                // Apply background colors and handle merging in one pass
                for (var c = 0; c < columnsToMerge.length; c++) {
                  var cell = rowCells.eq(columnsToMerge[c]);
                  cell.css('background-color', bgColor);

                  var cellVal = cell.text().trim();
                  var state = mergeState[c];

                  if (cellVal === state.lastVal && cellVal !== '' &&
                      currentGroup === state.lastGroup) {
                    cell.css('display', 'none');
                    state.span++;
                    $(state.lastNode).attr('rowspan', state.span);
                  } else {
                    state.lastVal = cellVal;
                    state.lastNode = colNodes[c][i];
                    state.lastGroup = currentGroup;
                    state.span = 1;
                    cell.css('vertical-align', 'middle');
                  }
                }
              }
            }",
            drug_group_col_idx
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
