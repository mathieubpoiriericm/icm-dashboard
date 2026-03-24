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
  if (!is.null(preloaded_table2)) {
    message("Using preloaded Table 2 data (direct reference)")
  }
  function(input, output, session) {
    # Extract data from app_data
    table1 <- app_data$table1
    gwas_trait_rows <- app_data$gwas_trait_rows
    omics_type_rows <- app_data$omics_type_rows

    # =========================================================================
    # PYTHON PLOT HANDLER
    # =========================================================================
    setup_python_plot_handler(input, session)

    # =========================================================================
    # PIPELINE PROGRESS TRACKER
    # =========================================================================
    pipeline_progress_data <- shiny::reactiveFileReader(
      intervalMillis = PIPELINE_PROGRESS_POLL_MS,
      session = session,
      filePath = PIPELINE_PROGRESS_FILE,
      readFunc = function(path) {
        tryCatch(
          jsonlite::fromJSON(path, simplifyVector = TRUE),
          error = function(e) NULL
        )
      }
    )

    output$pipeline_progress <- shiny::renderUI({
      progress <- pipeline_progress_data()

      stage_ids <- names(PIPELINE_STAGES)
      n_stages <- length(PIPELINE_STAGES)

      status <- if (!is.null(progress)) progress$status else "inactive"
      current_idx <- if (!is.null(progress)) {
        match(progress$stage, stage_ids, nomatch = 0L)
      } else {
        0L
      }

      if (identical(status, "inactive")) {
        header_icon <- shiny::icon("clock", class = "me-2")
        header_text <- "No Pipeline Run Recorded"
        header_class <- "pipeline-progress-title"
        detail <- shiny::div(
          class = "pipeline-progress-detail",
          "The pipeline has not been run yet"
        )
      } else if (identical(status, "running")) {
        header_icon <- shiny::tags$span(class = "pipeline-spinner")
        header_text <- "Pipeline Running"
        header_class <- "pipeline-progress-title"
        detail <- shiny::div(
          class = "pipeline-progress-detail",
          sprintf("Stage %s of %s", progress$stage_number, progress$total_stages)
        )
      } else if (identical(status, "completed")) {
        header_icon <- shiny::icon("check-circle", class = "me-2")
        header_text <- "Pipeline Completed Successfully"
        header_class <- "pipeline-progress-title pipeline-progress-success"
        detail <- if (!is.null(progress$updated_at)) {
          shiny::div(
            class = "pipeline-progress-detail",
            format(
              as.POSIXct(
                sub("\\+00:00$", "", progress$updated_at),
                format = "%Y-%m-%dT%H:%M:%S",
                tz = "UTC"
              ),
              "%B %d, %Y at %H:%M UTC"
            )
          )
        }
      } else if (identical(status, "error")) {
        header_icon <- shiny::icon("exclamation-triangle", class = "me-2")
        header_text <- "Pipeline Failed"
        header_class <- "pipeline-progress-title pipeline-progress-danger"
        detail <- if (!is.null(progress$error_message)) {
          shiny::div(
            class = "pipeline-progress-error-msg",
            shiny::tags$code(substr(progress$error_message, 1L, 200L))
          )
        }
      } else {
        # Defensive fallback for unexpected status values
        header_icon <- shiny::icon("clock", class = "me-2")
        header_text <- "No Pipeline Run Recorded"
        header_class <- "pipeline-progress-title"
        detail <- NULL
        status <- "inactive"
      }

      steps <- lapply(seq_len(n_stages), function(i) {
        step_state <- switch(status,
          inactive  = "inactive",
          completed = "completed",
          running = ,
          error = {
            if (i < current_idx) "completed"
            else if (i == current_idx) {
              if (identical(status, "error")) "error" else "active"
            }
            else "inactive"
          }
        )

        step_icon <- if (identical(step_state, "completed")) {
          shiny::icon("check", class = "pipeline-step-check")
        } else {
          shiny::span(class = "pipeline-step-number", i)
        }

        shiny::div(
          class = paste("pipeline-step", paste0("pipeline-step-", step_state)),
          shiny::div(class = "pipeline-step-circle", step_icon),
          shiny::div(class = "pipeline-step-label", PIPELINE_STAGES[[i]])
        )
      })

      card_class <- switch(status,
        inactive  = "pipeline-progress-inactive",
        completed = "pipeline-progress-completed",
        error     = "pipeline-progress-error",
        ""
      )

      shiny::div(
        class = "text-center",
        bslib::card(
          class = paste("pipeline-progress-card d-inline-block", card_class),
          fill = FALSE,
          bslib::card_body(
            class = "py-3 px-4",
            shiny::div(class = header_class, header_icon, header_text),
            shiny::div(
              class = "pipeline-stepper",
              style = paste0("--n-steps: ", n_stages),
              steps
            ),
            detail
          )
        )
      )
    })

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
    # CLINICAL TRIALS MAP SERVER LOGIC
    # =========================================================================
    map_data_loader <- build_map_data_loader(load_table2)

    # Setup lazy loading trigger for map tab
    setup_map_lazy_load_trigger(input, map_data_loader)

    # Render base map immediately (no data dependency)
    output$trials_map <- build_trials_map_base()

    # Add markers via proxy when map tab is active
    build_map_marker_observer(map_data_loader, input, session)

    # Render map statistics
    output$map_stats <- build_map_stats(map_data_loader)

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
        c(
          "Clinical Trials Table",
          "Clinical Trials Visualization",
          "Clinical Trials Map"
        )
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
  shiny::outputOptions(output, "trials_map", suspendWhenHidden = FALSE)
  shiny::outputOptions(output, "map_stats", suspendWhenHidden = TRUE)
  shiny::outputOptions(output, "pipeline_progress", suspendWhenHidden = TRUE)
}
# nolint end: object_usage_linter.
