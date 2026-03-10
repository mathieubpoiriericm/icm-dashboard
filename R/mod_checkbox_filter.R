# mod_checkbox_filter.R
# Shiny module for checkbox filter groups with "Show All" toggle behavior

# Checkbox Filter Module UI
#
# Args:
#   id: Module namespace ID
#   label: Label for the checkbox group
#   choices: Named vector of choices
#   selected: Initial selected values
#
# Returns:
#   A checkbox group input
checkbox_filter_ui <- function(
  id,
  label,
  choices,
  selected = NULL
) {
  ns <- shiny::NS(id)

  bslib::card(
    id = id,
    fill = FALSE,
    class = "mb-3",
    bslib::card_body(
      class = "py-3 px-3",
      shiny::div(
        class = "d-flex align-items-center",
        shiny::tags$label(label, class = "control-label"),
        shiny::uiOutput(ns("filter_count"), inline = TRUE)
      ),
      shinyWidgets::prettyCheckboxGroup(
        ns("filter"),
        label = NULL,
        choices = choices,
        selected = selected,
        shape = "curve",
        fill = TRUE,
        animation = "smooth",
        status = "primary",
        icon = shiny::icon("check")
      )
    )
  )
}

# Render Filter Count Badge
#
# Shared helper for rendering the active filter count badge.
# Returns NULL when the filter is in its default (inactive) state,
# or a styled span with the count when filters are active.
#
# Args:
#   sel: Character vector. Current filter selection.
#   is_inactive: Logical. TRUE when no badge should be shown.
#
# Returns:
#   NULL or a shiny span tag with the badge count.
render_filter_count_badge <- function(sel, is_inactive) {
  if (is_inactive) return(NULL)
  shiny::span(class = "filter-count-badge", length(sel))
}

# Checkbox Filter Module Server (with "Show All" toggle)
#
# Handles the toggle behavior where selecting "Show All" deselects others
# and selecting any other option deselects "Show All"
#
# Args:
#   id: Module namespace ID
#   default_selection: Default selection when resetting
#
# Returns:
#   Reactive containing current filter selection
checkbox_filter_server <- function(id, default_selection = "all") {
  shiny::moduleServer(id, function(input, output, session) {
    previous_selection <- shiny::reactiveVal(default_selection)

    # Debounced filter value (shared by badge and return value)
    debounced_filter <- shiny::reactive(input$filter) |> shiny::debounce(150)

    # Render active filter count badge from debounced signal
    output$filter_count <- shiny::renderUI({
      sel <- debounced_filter()
      render_filter_count_badge(sel, is.null(sel) || "all" %in% sel)
    })

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
          shinyWidgets::updatePrettyCheckboxGroup(
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
          shinyWidgets::updatePrettyCheckboxGroup(
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

    # Return the debounced reactive filter value
    debounced_filter
  })
}

# Binary Checkbox Filter Module Server (Yes/No toggle)
#
# Handles binary filters where selecting only one option toggles to both
#
# Args:
#   id: Module namespace ID
#   choices: The two choices (e.g., c("Yes", "No"))
#
# Returns:
#   Reactive containing current filter selection
binary_checkbox_filter_server <- function(id, choices = c("Yes", "No")) {
  shiny::moduleServer(id, function(input, output, session) {
    previous_selection <- shiny::reactiveVal(choices)

    # Debounced filter value (shared by badge and return value)
    debounced_filter <- shiny::reactive(input$filter) |> shiny::debounce(150)

    # Render active filter count badge from debounced signal
    output$filter_count <- shiny::renderUI({
      sel <- debounced_filter()
      render_filter_count_badge(
        sel,
        is.null(sel) || length(sel) == length(choices)
      )
    })

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
          shinyWidgets::updatePrettyCheckboxGroup(
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

    # Return the debounced reactive filter value
    debounced_filter
  })
}
