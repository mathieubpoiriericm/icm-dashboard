# server_table1.R
# Server logic for Table 1 (Gene Table)
# nolint start: object_usage_linter.

#' Build Table 1 Filtered Data Reactive
#'
#' Creates a reactive expression that filters table1 based on MR, GWAS trait,
#' and omics filter selections.
#'
#' @param table1 data.table. The main gene data table.
#' @param table1_display Data frame. Pre-computed display table with tooltips.
#' @param gwas_trait_rows fastmap. Pre-computed row indices for GWAS traits.
#' @param omics_type_rows fastmap. Pre-computed row indices for omics types.
#' @param mr_filter Reactive. Mendelian randomization filter values.
#' @param gwas_trait_filter Reactive. GWAS trait filter values.
#' @param omics_filter Reactive. Omics study filter values.
#'
#' @return A cached reactive expression returning filtered display data.
#'
#' @keywords internal
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
    # Use pre-assigned row_id column (no need to copy and assign)
    filtered_table1 <- data.table::copy(table1)

    # MR filter (skip if both Yes and No are selected - means show all)
    if (!is.null(mr_filter()) && length(mr_filter()) == 1L) {
      filtered_table1 <- filtered_table1[
        get("Mendelian Randomization") %in% mr_filter()
      ]
    }

    # GWAS trait filter (using fastmap $mget for O(1) lookups)
    if (
      !is.null(gwas_trait_filter()) &&
        length(gwas_trait_filter()) > 0L &&
        !"all" %in% gwas_trait_filter()
    ) {
      matching_rows <- unique(
        unlist(gwas_trait_rows$mget(gwas_trait_filter()))
      )
      filtered_table1 <- filtered_table1[row_id %in% matching_rows]
    }

    # Omics filter (using fastmap $mget for O(1) lookups)
    if (
      !is.null(omics_filter()) &&
        length(omics_filter()) > 0L &&
        !"all" %in% omics_filter()
    ) {
      matching_rows <- unique(unlist(omics_type_rows$mget(omics_filter())))
      filtered_table1 <- filtered_table1[row_id %in% matching_rows]
    }

    kept_rows <- filtered_table1$row_id
    result <- table1_display[kept_rows, , drop = FALSE]
    row.names(result) <- NULL
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

#' Build Table 1 Filter Message UI
#'
#' Creates a reactive UI expression that displays active filter status
#' for Table 1.
#'
#' @param mr_filter Reactive. Mendelian randomization filter values.
#' @param gwas_trait_filter Reactive. GWAS trait filter values.
#' @param omics_filter Reactive. Omics study filter values.
#'
#' @return A cached renderUI expression.
#'
#' @keywords internal
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

#' Build Filter List from Filter Specifications
#'
#' Helper function to build a character vector of active filter descriptions.
#'
#' @param filter_specs List of lists, each containing:
#'   - name: Display name for the filter
#'   - value: Current filter value(s)
#'   - is_single: If TRUE, only show when exactly one value selected
#'   - exclude_all: If TRUE, exclude "all" from display
#'
#' @return Character vector of filter descriptions.
#'
#' @keywords internal
build_filter_list <- function(filter_specs) {
  filters_applied <- character(0L)

  for (spec in filter_specs) {
    value <- spec$value
    name <- spec$name
    is_single <- isTRUE(spec$is_single)
    exclude_all <- isTRUE(spec$exclude_all)

    if (is.null(value) || length(value) == 0L) {
      next
    }

    if (is_single && length(value) != 1L) {
      next
    }

    if (exclude_all && "all" %in% value) {
      next
    }

    filters_applied <- c(
      filters_applied,
      paste0(name, ": ", paste(value, collapse = ", "))
    )
  }

  filters_applied
}

#' Render Filter Message HTML
#'
#' Creates the styled div for displaying active filter status.
#'
#' @param filters_applied Character vector of filter descriptions.
#'
#' @return A Shiny div element.
#'
#' @keywords internal
render_filter_message <- function(filters_applied) {
  if (length(filters_applied) > 0L) {
    shiny::div(
      style = filter_active_style,
      shiny::HTML(paste0(
        "<strong>Active Filters:</strong> ",
        paste(filters_applied, collapse = " | ")
      ))
    )
  } else {
    shiny::div(
      style = filter_none_style,
      shiny::HTML("<strong>Active Filters:</strong> None")
    )
  }
}

#' Build Table 1 DataTable Output
#'
#' Creates the DT::renderDT expression for Table 1 with custom header,
#' column definitions, and tooltip initialization.
#'
#' @param filtered_data Reactive. The filtered display data.
#'
#' @return A DT::renderDT expression.
#'
#' @keywords internal
build_table1_datatable <- function(filtered_data) {
  DT::renderDT(
    {
      # Create custom header with spanning column for Gene Information
      sketch <- htmltools::withTags(table(
        class = "display",
        thead(
          tr(
            th(rowspan = 2L, "#"),
            th(
              colspan = 3L,
              style = paste0(
                "text-align: center; ",
                "border-right: 2px solid #e1e4e8; ",
                "border-bottom: 2px solid #e1e4e8;"
              ),
              "Putative Causal Genes"
            ),
            th(
              colspan = 4L,
              style = paste0(
                "text-align: center; ",
                "border-right: 2px solid #e1e4e8; ",
                "border-bottom: 2px solid #e1e4e8;"
              ),
              "Evidence From Omics Studies"
            ),
            th(
              colspan = 2L,
              style = "text-align: center; border-bottom: 2px solid #e1e4e8;",
              "Expression Context"
            ),
            th(rowspan = 2L, "References")
          ),
          tr(
            th("Gene"),
            th("Protein"),
            th(
              style = "border-right: 2px solid #e1e4e8;",
              "Chromosomal Location"
            ),
            th("GWAS Trait"),
            th("Mendelian Randomization"),
            th("Evidence From Other Omics Studies"),
            th(
              style = "border-right: 2px solid #e1e4e8;",
              "Link to Monogenic Disease"
            ),
            th("Brain Cell Types"),
            th("Affected Pathway")
          )
        )
      ))

      dt <- DT::datatable(
        filtered_data(),
        container = sketch,
        escape = FALSE,
        rownames = FALSE,
        options = list(
          columnDefs = list(
            list(orderable = FALSE, targets = c(4L, 6L, 7L, 10L)),
            list(width = "25px", targets = 0L),
            list(width = "300px", targets = 10L),
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
          autoWidth = FALSE,
          pageLength = DATATABLE_PAGE_LENGTH,
          deferRender = TRUE,
          dom = "lfrtip",
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
    server = FALSE
  )
}
# nolint end: object_usage_linter.
