# mod_checkbox_filter.R
# Shiny module for checkbox filter groups with "Show All" toggle behavior

#' Checkbox Filter Module UI
#'
#' @param id Module namespace ID
#' @param label Label for the checkbox group
#' @param choices Named vector of choices
#' @param selected Initial selected values
#' @param has_show_all Whether to include "Show All" toggle behavior
#'
#' @return A checkbox group input
#'
#' @export
checkbox_filter_ui <- function(
  id,
  label,
  choices,
  selected = NULL,
  has_show_all = FALSE
) {
  ns <- shiny::NS(id)

  bslib::card(
    id = id,
    fill = FALSE,
    class = "mb-3",
    bslib::card_body(
      class = "py-3 px-3",
      shiny::checkboxGroupInput(
        ns("filter"),
        label,
        choices = choices,
        selected = selected
      )
    )
  )
}

#' Checkbox Filter Module Server (with "Show All" toggle)
#'
#' Handles the toggle behavior where selecting "Show All" deselects others

#' and selecting any other option deselects "Show All"
#'
#' @param id Module namespace ID
#' @param default_selection Default selection when resetting
#'
#' @return Reactive containing current filter selection
#'
#' @export
checkbox_filter_server <- function(id, default_selection = "all") {
  shiny::moduleServer(id, function(input, output, session) {
    previous_selection <- shiny::reactiveVal(default_selection)

    shiny::observeEvent(
      input$filter,
      {
        selected <- input$filter
        prev <- shiny::isolate(previous_selection())

        if (identical(selected, prev)) {
          return()
        }

        newly_added <- setdiff(selected, prev)

        # If "all" was just selected, reset to only "all"
        if ("all" %in% newly_added) {
          shiny::isolate(previous_selection("all"))
          # Freeze to prevent cascading invalidation
          shiny::freezeReactiveValue(input, "filter")
          shiny::updateCheckboxGroupInput(
            session,
            "filter",
            selected = "all"
          )
          return()
        }

        # If "all" is selected but something else was added, deselect "all"
        if (
          "all" %in%
            selected &&
            length(newly_added) > 0 &&
            !"all" %in% newly_added
        ) {
          new_selection <- setdiff(selected, "all")
          shiny::isolate(previous_selection(new_selection))
          # Freeze to prevent cascading invalidation
          shiny::freezeReactiveValue(input, "filter")
          shiny::updateCheckboxGroupInput(
            session,
            "filter",
            selected = new_selection
          )
          return()
        }

        shiny::isolate(previous_selection(selected))
      },
      ignoreNULL = FALSE
    )

    # Return the reactive filter value (debounced to avoid rapid re-filtering)
    shiny::reactive(input$filter) |> shiny::debounce(150)
  })
}

#' Binary Checkbox Filter Module Server (Yes/No toggle)
#'
#' Handles binary filters where selecting only one option toggles to both
#'
#' @param id Module namespace ID
#' @param choices The two choices (e.g., c("Yes", "No"))
#'
#' @return Reactive containing current filter selection
#'
#' @export
binary_checkbox_filter_server <- function(id, choices = c("Yes", "No")) {
  shiny::moduleServer(id, function(input, output, session) {
    previous_selection <- shiny::reactiveVal(choices)

    shiny::observeEvent(
      input$filter,
      {
        selected <- input$filter
        prev <- shiny::isolate(previous_selection())

        if (identical(selected, prev)) {
          return()
        }

        newly_added <- setdiff(selected, prev)

        # If toggling from one selection to another single selection,
        # reset to both
        if (
          length(selected) == 1 &&
            length(prev) == 1 &&
            length(newly_added) == 1
        ) {
          shiny::isolate(previous_selection(choices))
          # Freeze to prevent cascading invalidation
          shiny::freezeReactiveValue(input, "filter")
          shiny::updateCheckboxGroupInput(
            session,
            "filter",
            selected = choices
          )
          return()
        }

        shiny::isolate(previous_selection(selected))
      },
      ignoreNULL = FALSE
    )

    # Debounced to avoid rapid re-filtering
    shiny::reactive(input$filter) |> shiny::debounce(150)
  })
}
