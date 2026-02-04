# server_map.R
# Server logic for Clinical Trials Map tab
# nolint start: object_usage_linter.

# =============================================================================
# MAP CONFIGURATION
# =============================================================================
# Constants loaded from constants.R:
# MAP_DEFAULT_LAT, MAP_DEFAULT_LNG, MAP_DEFAULT_ZOOM, MAP_HEIGHT_PX

# Marker cluster options
map_cluster_options <- leaflet::markerClusterOptions(
  showCoverageOnHover = TRUE,
  zoomToBoundsOnClick = TRUE,
  spiderfyOnMaxZoom = TRUE,
  removeOutsideVisibleBounds = TRUE,
  maxClusterRadius = 50
)

# =============================================================================
# MAP SERVER FUNCTIONS
# =============================================================================

#' Build Map Reactive Data Loader
#'
#' Creates a reactive that lazily loads and geocodes trial locations
#' when the map tab is first accessed. Includes req() validation to
#' prevent silent failures.
#'
#' @param load_table2 Reactive function that returns Table 2 data.
#'
#' @return Reactive returning geocoded map data.
build_map_data_loader <- function(load_table2) {
  # Use reactiveVal to cache the loaded data
  map_data_cache <- shiny::reactiveVal(NULL)
  is_loading <- shiny::reactiveVal(FALSE)

  shiny::reactive({
    # Return cached data if available
    cached <- map_data_cache()
    if (!is.null(cached)) {
      return(cached)
    }

    # Prevent concurrent loading
    if (is_loading()) {
      return(NULL)
    }

    is_loading(TRUE)
    # Ensure is_loading is reset even on errors (reactive cleanup)
    on.exit(is_loading(FALSE), add = TRUE)

    # Get Table 2 data with validation
    table2_data <- load_table2()
    shiny::req(table2_data, table2_data$table2)

    table2 <- table2_data$table2
    shiny::req(nrow(table2) > 0L)

    # Load or fetch geocoded locations
    locations <- tryCatch({
      load_or_fetch_geocoded_trials(table2)
    }, error = function(e) {
      message(sprintf("Error loading geocoded trials: %s", conditionMessage(e)))
      NULL
    })

    # Prepare map data
    map_data <- prepare_map_data(locations, table2)

    # Cache the result
    map_data_cache(map_data)

    map_data
  })
}

#' Build Base Leaflet Map Output
#'
#' Creates the initial renderLeaflet output with base map only (no markers).
#' Markers are added separately via leafletProxy for better performance.
#'
#' @return Shiny render function for leaflet output.
build_trials_map_base <- function() {
  leaflet::renderLeaflet({
    leaflet::leaflet() |>
      leaflet::addProviderTiles(
        leaflet::providers$CartoDB.Positron,
        options = leaflet::providerTileOptions(
          noWrap = FALSE,
          updateWhenIdle = TRUE,
          updateWhenZooming = FALSE
        )
      ) |>
      leaflet::setView(
        lng = MAP_DEFAULT_LNG,
        lat = MAP_DEFAULT_LAT,
        zoom = MAP_DEFAULT_ZOOM
      )
  })
}

#' Build Map Marker Update Observer
#'
#' Creates an observer that updates map markers using leafletProxy when
#' map data changes. This is more efficient than re-rendering the entire map.
#'
#' @param map_data_reactive Reactive returning prepared map data.
#' @param session Shiny session object.
#'
#' @return Observer (side effects only).
build_map_marker_observer <- function(map_data_reactive, session) {
  shiny::observe({
    map_data <- map_data_reactive()

    # Clear existing markers and add new ones
    proxy <- leaflet::leafletProxy("trials_map", session = session)

    # Clear any existing marker clusters
    proxy <- proxy |> leaflet::clearMarkerClusters()

    # Add markers if data is available
    if (!is.null(map_data) && nrow(map_data) > 0L) {
      proxy |>
        leaflet::addCircleMarkers(
          data = map_data,
          lng = ~lon,
          lat = ~lat,
          popup = ~popup_content,
          radius = 8,
          color = "#2d287a",
          fillColor = "#667eea",
          fillOpacity = 0.7,
          weight = 2,
          clusterOptions = map_cluster_options
        )
    }
  })
}

#' Build Leaflet Map Output (Legacy Wrapper)
#'
#' Creates the renderLeaflet output for the clinical trials map.
#' This is a backward-compatible wrapper that creates a base map.
#' For optimal performance, use build_trials_map_base() with
#' build_map_marker_observer() separately.
#'
#' @param map_data_reactive Reactive returning prepared map data.
#'
#' @return Shiny render function for leaflet output.
build_trials_map <- function(map_data_reactive) {
  leaflet::renderLeaflet({
    map_data <- map_data_reactive()

    # Create base map with optimized tile options
    map <- leaflet::leaflet() |>
      leaflet::addProviderTiles(
        leaflet::providers$CartoDB.Positron,
        options = leaflet::providerTileOptions(
          noWrap = FALSE,
          updateWhenIdle = TRUE,
          updateWhenZooming = FALSE
        )
      ) |>
      leaflet::setView(
        lng = MAP_DEFAULT_LNG,
        lat = MAP_DEFAULT_LAT,
        zoom = MAP_DEFAULT_ZOOM
      )

    # Add markers if data is available
    if (!is.null(map_data) && nrow(map_data) > 0L) {
      map <- map |>
        leaflet::addCircleMarkers(
          data = map_data,
          lng = ~lon,
          lat = ~lat,
          popup = ~popup_content,
          radius = 8,
          color = "#2d287a",
          fillColor = "#667eea",
          fillOpacity = 0.7,
          weight = 2,
          clusterOptions = map_cluster_options
        )
    }

    map
  })
}

#' Build Map Statistics Text
#'
#' Creates reactive text showing the number of mapped trial sites.
#' Uses the map data reactive as the single source of truth to avoid
#' redundant reactive dependencies that cause state machine conflicts.
#'
#' @param map_data_reactive Reactive returning prepared map data.
#'
#' @return Shiny render function for UI output.
build_map_stats <- function(map_data_reactive) {
  shiny::renderUI({
    map_data <- map_data_reactive()

    # Show loading state when map data is not yet available
    if (is.null(map_data) || nrow(map_data) == 0L) {
      return(shiny::div(
        class = "map-stats",
        "Loading trial location data..."
      ))
    }

    # Extract statistics from map_data (single reactive dependency)
    n_sites <- nrow(map_data)
    n_unique_trials <- length(unique(map_data$nct_id))

    stats_text <- paste0(
      "Showing <strong>%d</strong> research sites ",
      "from <strong>%d</strong> NCT trials"
    )
    shiny::div(
      class = "map-stats",
      shiny::HTML(sprintf(stats_text, n_sites, n_unique_trials))
    )
  })
}

# Minimum interval between map data fetches (seconds)
map_min_fetch_interval_seconds <- 10L

#' Setup Map Tab Lazy Load Trigger
#'
#' Creates an observer that triggers map data loading when the
#' Clinical Trials Map tab is first accessed. Includes rate limiting
#' to prevent API abuse from rapid tab switching.
#'
#' @param input Shiny input object.
#' @param map_data_loader Reactive that loads map data.
#'
#' @return NULL (side effects only).
setup_map_lazy_load_trigger <- function(input, map_data_loader) {
  # Track last fetch time for rate limiting
  last_fetch_time <- shiny::reactiveVal(NULL)

  shiny::observeEvent(input$tabs, {
    if (identical(input$tabs, "Clinical Trials Map")) {
      # Rate limiting: enforce minimum interval between fetches
      last <- last_fetch_time()
      now <- Sys.time()

      if (!is.null(last)) {
        elapsed <- as.numeric(difftime(now, last, units = "secs"))
        if (elapsed < map_min_fetch_interval_seconds) {
          return()
        }
      }

      # Update last fetch time and trigger loading
      last_fetch_time(now)
      map_data_loader()
    }
  })
}

# nolint end: object_usage_linter.
