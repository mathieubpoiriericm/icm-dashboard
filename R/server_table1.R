# server_table1.R
# Server logic for Table 1 (Gene Table)
# nolint start: object_usage_linter.

# Build Table 1 Filtered Data Reactive
#
# Creates a reactive expression that filters table1 based on MR, GWAS trait,
# and omics filter selections.
#
# Args:
#   table1: data.table. The main gene data table.
#   table1_display: Data frame. Pre-computed display table with tooltips.
#   gwas_trait_rows: fastmap. Pre-computed row indices for GWAS traits.
#   omics_type_rows: fastmap. Pre-computed row indices for omics types.
#   mr_filter: Reactive. Mendelian randomization filter values.
#   gwas_trait_filter: Reactive. GWAS trait filter values.
#   omics_filter: Reactive. Omics study filter values.
#
# Returns:
#   A cached reactive expression returning filtered display data.
build_table1_filtered_data <- function(
  table1,
  table1_display,
  gwas_trait_rows,
  omics_type_rows,
  mr_filter,
  gwas_trait_filter,
  omics_filter
) {
  shiny::reactive({
    # Validate required data is available
    shiny::req(table1, table1_display)

    # Start with all row IDs, filter by intersection (avoids data.table copy)
    kept_rows <- table1$row_id

    # MR filter (skip if both Yes and No are selected - means show all)
    if (!is.null(mr_filter()) && length(mr_filter()) == 1L) {
      mr_rows <- table1[
        get("Mendelian Randomization") %in% mr_filter(),
        row_id
      ]
      kept_rows <- intersect(kept_rows, mr_rows)
    }

    # GWAS trait filter (using fastmap $mget for O(1) lookups)
    if (
      !is.null(gwas_trait_filter()) &&
        length(gwas_trait_filter()) > 0L &&
        !"all" %in% gwas_trait_filter()
    ) {
      gwas_rows <- unique(unlist(gwas_trait_rows$mget(gwas_trait_filter())))
      kept_rows <- intersect(kept_rows, gwas_rows)
    }

    # Omics filter (using fastmap $mget for O(1) lookups)
    if (
      !is.null(omics_filter()) &&
        length(omics_filter()) > 0L &&
        !"all" %in% omics_filter()
    ) {
      omics_rows <- unique(unlist(omics_type_rows$mget(omics_filter())))
      kept_rows <- intersect(kept_rows, omics_rows)
    }

    result <- table1_display[kept_rows, , drop = FALSE]
    row.names(result) <- NULL
    # Remove row_id column (used for filtering) before display
    result <- result[, !names(result) %in% "row_id", drop = FALSE]
    result <- cbind(
      data.frame(`#` = seq_len(nrow(result)), check.names = FALSE),
      result
    )
    result
  }) |>
    shiny::bindCache(
      mr_filter(),
      gwas_trait_filter(),
      omics_filter()
    )
}

# Build Table 1 Filter Message UI
#
# Creates a reactive UI expression that displays active filter status
# for Table 1.
#
# Args:
#   mr_filter: Reactive. Mendelian randomization filter values.
#   gwas_trait_filter: Reactive. GWAS trait filter values.
#   omics_filter: Reactive. Omics study filter values.
#
# Returns:
#   A cached renderUI expression.
build_table1_filter_message <- function(
  mr_filter,
  gwas_trait_filter,
  omics_filter
) {
  shiny::renderUI({
    filters_applied <- build_filter_list(
      list(
        list(
          name = "Mendelian Randomization",
          value = mr_filter(),
          is_single = TRUE
        ),
        list(
          name = "GWAS Traits",
          value = gwas_trait_filter(),
          exclude_all = TRUE
        ),
        list(
          name = "Omics Studies",
          value = omics_filter(),
          exclude_all = TRUE
        )
      )
    )

    render_filter_message(filters_applied)
  }) |>
    shiny::bindCache(mr_filter(), gwas_trait_filter(), omics_filter())
}

# Build Table 1 DataTable Output
#
# Creates the DT::renderDT expression for Table 1 with custom header,
# column definitions, and tooltip initialization.
#
# Args:
#   filtered_data: Reactive. The filtered display data.
#
# Returns:
#   A DT::renderDT expression.
build_table1_datatable <- function(filtered_data) {
  DT::renderDT(
    {
      # Get filtered data and validate it's not empty
      data <- filtered_data()
      shiny::validate(
        shiny::need(
          !is.null(data) && nrow(data) > 0L,
          paste(
            "No genes match the selected filters.",
            "Try adjusting your filter criteria."
          )
        )
      )

      # Create custom header with spanning column for Gene Information
      sketch <- htmltools::withTags(table(
        class = "display",
        thead(
          tr(
            th(rowspan = 2L, "#"),
            th(
              colspan = 3L,
              class = "col-group-border",
              style = "text-align: center;",
              "Putative Causal Genes"
            ),
            th(
              colspan = 4L,
              class = "col-group-border",
              style = "text-align: center;",
              "Evidence From Omics Studies"
            ),
            th(
              colspan = 2L,
              style = "text-align: center;",
              "Expression Context"
            ),
            th(rowspan = 2L, "References")
          ),
          tr(
            th("Gene"),
            th("Protein"),
            th(
              class = "col-group-border",
              "Chromosomal Location"
            ),
            th("GWAS Trait"),
            th("Mendelian Randomization"),
            th("Evidence From Other Omics Studies"),
            th(
              class = "col-group-border",
              "Link to Monogenic Disease"
            ),
            th("Brain Cell Types"),
            th("Affected Pathway")
          )
        )
      ))

      dt <- DT::datatable(
        data,
        container = sketch,
        escape = FALSE,
        rownames = FALSE,
        plugins = "natural",
        options = list(
          columnDefs = list(
            list(
              targets = c(6L, 7L, 8L, 9L, 10L),
              render = DT::JS(
                "function(data, type, row, meta) {
                  if (type === 'filter' || type === 'search') {
                    var temp = document.createElement('div');
                    temp.innerHTML = data;
                    return temp.textContent || temp.innerText || '';
                  }
                  return data;
                }"
              )
            ),
            list(
              targets = c(1L, 2L),
              orderable = TRUE,
              type = "html",
              render = DT::JS(
                "function(data, type, row, meta) {
                  if (type === 'sort' || type === 'type') {
                    var temp = document.createElement('div');
                    temp.innerHTML = data;
                    var el = temp.querySelector('[data-order]');
                    if (el) {
                      return el.getAttribute('data-order');
                    }
                    return (temp.textContent || '').toLowerCase();
                  }
                  return data;
                }"
              )
            )
          ),
          autoWidth = TRUE,
          pageLength = DATATABLE_PAGE_LENGTH,
          deferRender = TRUE,
          dom = "rtip",
          scrollX = TRUE,
          scrollCollapse = TRUE,
          searchDelay = DATATABLE_SEARCH_DELAY,
          orderCellsTop = FALSE,
          initComplete = DT::JS(
            "function() {
              $(this.api().table().header())
                .find('th').css('text-align', 'center');
            }"
          ),
          drawCallback = DT::JS(
            "function() {
              if (typeof initializeTippy === 'function') {
                initializeTippy();
              }
            }"
          )
        )
      ) |>
        DT::formatStyle(
          columns = c(
            "GWAS Trait",
            "Evidence From Other Omics Studies",
            "Link to Monogenic Disease"
          ),
          target = "cell",
          fontStyle = DT::JS("value == '(none found)' ? 'italic' : 'normal'")
        ) |>
        DT::formatStyle(
          columns = c("Brain Cell Types", "Affected Pathway"),
          target = "cell",
          fontStyle = DT::JS("value == '(unknown)' ? 'italic' : 'normal'")
        ) |>
        DT::formatStyle(
          columns = "References",
          target = "cell",
          fontStyle = DT::JS(
            "value == '(reference needed)' ? 'italic' : 'normal'"
          )
        )

      dt
    },
    server = DATATABLE_SERVER_SIDE
  )
}
# nolint end: object_usage_linter.
