# server_map.R
# Server logic for Clinical Trials Map tab
# nolint start: object_usage_linter.

# =============================================================================
# MAP CONFIGURATION
# =============================================================================
# Other map constants live in constants.R: MAP_DEFAULT_LAT, MAP_DEFAULT_LNG,
# MAP_DEFAULT_ZOOM, MAP_HEIGHT_PX, MAP_CLUSTER_RADIUS_PX.

MAP_CLUSTER_OPTIONS <- leaflet::markerClusterOptions(
  showCoverageOnHover = TRUE,
  zoomToBoundsOnClick = TRUE,
  spiderfyOnMaxZoom = TRUE,
  removeOutsideVisibleBounds = TRUE,
  maxClusterRadius = MAP_CLUSTER_RADIUS_PX
)

# =============================================================================
# MAP SERVER FUNCTIONS
# =============================================================================

# Build Map Reactive Data Loader
#
# Creates a reactive that lazily loads and geocodes trial locations
# when the map tab is first accessed. Includes req() validation to
# prevent silent failures.
#
# Args:
#   load_table2: Reactive function. Returns Table 2 data.
#
# Returns:
#   Reactive returning geocoded map data.
build_map_data_loader <- function(load_table2) {
  # Cache across tab visits: geocoding is slow (100+ network calls) and the
  # underlying table2 data doesn't change during a session.
  map_data_cache <- shiny::reactiveVal(NULL)
  shiny::reactive({
    cached <- map_data_cache()
    if (!is.null(cached)) {
      return(cached)
    }

    table2_data <- load_table2()
    shiny::req(table2_data, table2_data$table2)

    table2 <- table2_data$table2
    shiny::req(nrow(table2) > 0L)

    locations <- tryCatch({
      load_or_fetch_geocoded_trials(table2)
    }, error = function(e) {
      message(sprintf("Error loading geocoded trials: %s", conditionMessage(e)))
      NULL
    })

    map_data <- prepare_map_data(locations, table2)
    map_data_cache(map_data)
    map_data
  })
}

# Build Base Leaflet Map Output
#
# Creates the initial renderLeaflet output with base map only (no markers).
# Markers are added separately via leafletProxy for better performance.
#
# Returns:
#   Shiny render function for leaflet output.
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

# Build Map Marker Update Observer
#
# Creates an observer that updates map markers using leafletProxy when
# map data changes. This is more efficient than re-rendering the entire map.
#
# Args:
#   map_data_reactive: Reactive returning prepared map data.
#   session: Shiny session object.
#
# Returns:
#   Observer (side effects only).
build_map_marker_observer <- function(map_data_reactive, input, session) {
  markers_drawn <- shiny::reactiveVal(FALSE)

  # Clear the drawn flag only when data identity actually changes; otherwise
  # every tab switch would force a redraw of the same markers.
  last_data <- shiny::reactiveVal(NULL)
  shiny::observe({
    current <- map_data_reactive()
    if (!identical(current, shiny::isolate(last_data()))) {
      last_data(current)
      if (shiny::isolate(markers_drawn())) {
        markers_drawn(FALSE)
      }
    }
  })

  shiny::observe({
    shiny::req(identical(input$tabs, TAB_VALUES$ct_map))
    shiny::req(!markers_drawn())
    map_data <- map_data_reactive()

    proxy <- leaflet::leafletProxy("trials_map", session = session) |>
      leaflet::clearMarkerClusters()

    if (!is.null(map_data) && nrow(map_data) > 0L) {
      proxy |>
        leaflet::addCircleMarkers(
          data = map_data,
          lng = ~lon,
          lat = ~lat,
          popup = ~popup_content,
          radius = 8,
          color = BRAND_COLOR_PRIMARY,
          fillColor = BRAND_COLOR_ACCENT,
          fillOpacity = 0.7,
          weight = 2,
          clusterOptions = MAP_CLUSTER_OPTIONS
        )
    }

    markers_drawn(TRUE)
  })
}

# Build Map Statistics Text
#
# Creates reactive text showing the number of mapped trial sites.
# Uses the map data reactive as the single source of truth to avoid
# redundant reactive dependencies that cause state machine conflicts.
#
# Args:
#   map_data_reactive: Reactive returning prepared map data.
#
# Returns:
#   Shiny render function for UI output.
build_map_stats <- function(map_data_reactive) {
  shiny::renderUI({
    map_data <- map_data_reactive()

    if (is.null(map_data)) {
      return(shiny::div(
        class = "map-stats",
        "Loading trial location data..."
      ))
    }

    if (nrow(map_data) == 0L) {
      return(shiny::div(
        class = "map-stats",
        "No trial locations available to display."
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

# Setup Map Tab Lazy Load Trigger
#
# Creates an observer that triggers map data loading when the
# Clinical Trials Map tab is first accessed. The map_data_loader
# reactive caches its result, so subsequent triggers are no-ops.
#
# Args:
#   input: Shiny input object.
#   map_data_loader: Reactive that loads map data (with internal caching).
#
# Returns:
#   NULL (side effects only).
setup_map_lazy_load_trigger <- function(input, map_data_loader) {
  shiny::observeEvent(input$tabs, {
    if (identical(input$tabs, TAB_VALUES$ct_map)) {
      map_data_loader()
    }
  })
}

# nolint end: object_usage_linter.
