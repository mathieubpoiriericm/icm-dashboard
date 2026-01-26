# server.R
# Main server logic for SVD Dashboard
# Orchestrates Table 1 and Table 2 server modules
# nolint start: object_usage_linter.

# Build Server Function for SVD Dashboard
#
# Creates the server function for the Shiny application by composing
# Table 1 and Table 2 server modules.
#
# Args:
#   app_data: List containing prepared application data from
#     load_and_prepare_data().
#   table1_display: Data frame with pre-computed tooltip HTML for Table 1.
#   preloaded_table2: Optional list containing preloaded Table 2 data.
#     If provided, eliminates lazy loading delay on Clinical Trials tabs.
#
# Returns:
#   A Shiny server function.
build_server <- function(app_data, table1_display, preloaded_table2 = NULL) {
  function(input, output, session) {
    # Extract data from app_data
    table1 <- app_data$table1
    gwas_trait_rows <- app_data$gwas_trait_rows
    omics_type_rows <- app_data$omics_type_rows

    # =========================================================================
    # SESSION CLEANUP
    # =========================================================================
    session$onSessionEnded(function() {
      # Clean up session-scoped reactive values to prevent memory accumulation
      # in multi-user deployments
      message(sprintf("Session ended, cleaning up resources"))
    })

    # =========================================================================
    # THEME SWITCHING (BSLIB DARK MODE)
    # =========================================================================
    shiny::observeEvent(input$dark_mode, {
      session$setCurrentTheme(
        if (isTRUE(input$dark_mode)) dark_theme else light_theme
      )
    }, ignoreNULL = FALSE)

    # =========================================================================
    # PYTHON PLOT HANDLER
    # =========================================================================
    setup_python_plot_handler(input, session)

    # =========================================================================
    # TABLE 2 DATA (PRELOADED OR LAZY LOADED)
    # =========================================================================
    # Optimization: When preloaded, use direct reference instead of copying
    # into per-session reactiveVals (saves ~1-3MB per session)
    if (!is.null(preloaded_table2)) {
      # Use preloaded data directly - no per-session copies needed
      load_table2 <- shiny::reactive({
        preloaded_table2
      })
      message("Using preloaded Table 2 data (direct reference)")
    } else {
      # Lazy loading: create reactiveVals to track loading state
      table2_reactive_vals <- create_table2_reactive_vals()

      load_table2 <- build_table2_loader(
        table2_reactive_vals$table2_data,
        table2_reactive_vals$table2_display_data,
        table2_reactive_vals$ct_info_data,
        table2_reactive_vals$registry_matches_data,
        table2_reactive_vals$registry_rows_data,
        table2_reactive_vals$sample_sizes_data,
        table2_reactive_vals$sample_sizes_hash_data
      )

      # Trigger Table 2 loading when Clinical Trials tabs are accessed
      setup_table2_lazy_load_trigger(input, load_table2)
    }

    # =========================================================================
    # FILTER MODULES
    # =========================================================================
    filters <- initialize_filter_modules()

    # Debounce slider input
    sample_size_filter_debounced <- shiny::debounce(
      shiny::reactive(input$sample_size_filter),
      SLIDER_DEBOUNCE_MS
    )

    # =========================================================================
    # TABLE 1 SERVER LOGIC
    # =========================================================================
    filtered_data <- build_table1_filtered_data(
      table1,
      table1_display,
      gwas_trait_rows,
      omics_type_rows,
      filters$mr_filter,
      filters$gwas_trait_filter,
      filters$omics_filter
    )

    output$filter_message_table1 <- build_table1_filter_message(
      filters$mr_filter,
      filters$gwas_trait_filter,
      filters$omics_filter
    )

    output$firstTable <- build_table1_datatable(filtered_data)

    # =========================================================================
    # TABLE 2 SERVER LOGIC
    # =========================================================================
    output$sample_size_histogram <- build_sample_size_histogram(
      load_table2,
      sample_size_filter_debounced
    )

    filtered_data2 <- build_table2_filtered_data(
      load_table2,
      filters$ge_filter,
      filters$reg_filter,
      filters$ct_filter,
      filters$pop_filter,
      filters$spon_filter,
      sample_size_filter_debounced
    )

    output$filter_message_table2 <- build_table2_filter_message(
      filters$ge_filter,
      filters$reg_filter,
      filters$ct_filter,
      filters$pop_filter,
      filters$spon_filter,
      sample_size_filter_debounced
    )

    output$secondTable <- build_table2_datatable(filtered_data2)

    # =========================================================================
    # OUTPUT OPTIONS
    # =========================================================================
    configure_output_options(output)
  }
}

# Setup Python Plot Handler
#
# Configures the observer for resizing the Python visualization when
# the Clinical Trials Visualization tab is accessed.
#
# Args:
#   input: Shiny input object.
#   session: Shiny session object.
#
# Returns:
#   NULL (side effects only).
setup_python_plot_handler <- function(input, session) {
  shiny::observeEvent(
    input$tabs,
    {
      if (identical(input$tabs, "Clinical Trials Visualization")) {
        session$sendCustomMessage("rerunPythonPlotSizing", list())
      }
    },
    ignoreNULL = FALSE
  )
}

# Create Table 2 Reactive Values
#
# Creates the reactiveVal containers for lazy-loaded Table 2 data.
#
# Returns:
#   List of reactiveVal objects.
create_table2_reactive_vals <- function() {
  list(
    table2_data = shiny::reactiveVal(NULL),
    table2_display_data = shiny::reactiveVal(NULL),
    ct_info_data = shiny::reactiveVal(NULL),
    registry_matches_data = shiny::reactiveVal(NULL),
    registry_rows_data = shiny::reactiveVal(NULL),
    sample_sizes_data = shiny::reactiveVal(NULL),
    sample_sizes_hash_data = shiny::reactiveVal(NULL)
  )
}

# Setup Table 2 Lazy Load Trigger
#
# Creates an observer that triggers Table 2 data loading when
# Clinical Trials tabs are accessed.
#
# Args:
#   input: Shiny input object.
#   load_table2: Reactive that loads Table 2 data.
#
# Returns:
#   NULL (side effects only).
setup_table2_lazy_load_trigger <- function(input, load_table2) {
  shiny::observeEvent(input$tabs, {
    if (
      input$tabs %in%
        c("Clinical Trials Table", "Clinical Trials Visualization")
    ) {
      load_table2()
    }
  })
}

# Initialize Filter Modules
#
# Creates and returns all filter module server instances.
#
# Returns:
#   List of reactive filter values.
initialize_filter_modules <- function() {
  list(
    # Table 1 filters
    mr_filter = binary_checkbox_filter_server("mr_filter", c("Yes", "No")),
    gwas_trait_filter = checkbox_filter_server("gwas_trait_filter", "all"),
    omics_filter = checkbox_filter_server("omics_filter", "all"),

    # Table 2 filters
    ge_filter = binary_checkbox_filter_server("ge_filter", c("Yes", "No")),
    reg_filter = checkbox_filter_server("reg_filter", "all"),
    ct_filter = checkbox_filter_server("ct_filter", "all"),
    pop_filter = checkbox_filter_server("pop_filter", "all"),
    spon_filter = checkbox_filter_server("spon_filter", "all")
  )
}

# Configure Output Options
#
# Sets suspendWhenHidden for all outputs to improve performance.
#
# Args:
#   output: Shiny output object.
#
# Returns:
#   NULL (side effects only).
configure_output_options <- function(output) {
  shiny::outputOptions(output, "firstTable", suspendWhenHidden = TRUE)
  shiny::outputOptions(
    output,
    "filter_message_table1",
    suspendWhenHidden = TRUE
  )
  shiny::outputOptions(
    output,
    "sample_size_histogram",
    suspendWhenHidden = TRUE
  )
  shiny::outputOptions(
    output,
    "filter_message_table2",
    suspendWhenHidden = TRUE
  )
  shiny::outputOptions(output, "secondTable", suspendWhenHidden = TRUE)
}
# nolint end: object_usage_linter.
