# fetch_trial_locations.R
# Functions for fetching clinical trial locations from ClinicalTrials.gov API
# and geocoding them for map display
# nolint start: object_usage_linter.

# Load constants from constants.R (imported via source() in app.R)
# Uses: MAP_CT_API_BASE_URL, MAP_API_DELAY_MS, MAP_CACHE_PATH

# =============================================================================
# HTTP CONFIGURATION
# =============================================================================

# HTTP timeout for API requests (seconds)
http_timeout_seconds <- 30L

# Maximum retry attempts for failed API requests
max_retry_attempts <- 3L

# Base delay for exponential backoff (seconds)
retry_base_delay_seconds <- 1L

# =============================================================================
# API FETCHING FUNCTIONS
# =============================================================================

# Extract NCT IDs from Table 2 Data
#
# Filters the clinical trials table to extract unique NCT (ClinicalTrials.gov)
# registry IDs.
#
# Args:
#   table2: Data frame containing clinical trial data with Registry ID column.
#
# Returns:
#   Character vector of unique NCT IDs (e.g., "NCT01234567").
extract_nct_ids <- function(table2) {
  registry_ids <- table2$`Registry ID`
  # Stricter validation: NCT followed by exactly 8 digits
  pattern <- "^NCT[0-9]{8}$"
  nct_ids <- registry_ids[grepl(pattern, registry_ids, ignore.case = TRUE)]
  unique(toupper(nct_ids))
}

# Fetch Trial Locations from ClinicalTrials.gov API
#
# Queries the ClinicalTrials.gov API v2 to retrieve facility location
# information for a single trial, along with trial title and recruitment
# status. Includes timeout and exponential backoff retry logic.
#
# Args:
#   nct_id: Character. A single NCT ID (e.g., "NCT01234567").
#   timeout_seconds: Integer. HTTP request timeout in seconds.
#   max_retries: Integer. Maximum number of retry attempts on failure.
#
# Returns:
#   Data frame with columns: nct_id, facility_name, city, state, country,
#   trial_title, status, or NULL if request fails or no locations found.
fetch_trial_locations <- function(
  nct_id,
  timeout_seconds = http_timeout_seconds,
  max_retries = max_retry_attempts
) {
  # Validate NCT ID format (defense-in-depth)
  if (!grepl("^NCT[0-9]{8}$", nct_id, ignore.case = TRUE)) {
    warning(sprintf("Invalid NCT ID format: %s", nct_id))
    return(NULL)
  }

  # URL-encode NCT ID to prevent injection
  encoded_nct_id <- utils::URLencode(nct_id, reserved = TRUE)
  url <- paste0(
    MAP_CT_API_BASE_URL,
    encoded_nct_id,
    "?fields=protocolSection.contactsLocationsModule.locations,",
    "protocolSection.identificationModule.briefTitle,",
    "protocolSection.statusModule.overallStatus"
  )

  # Retry loop with exponential backoff
  for (attempt in seq_len(max_retries)) {
    result <- tryCatch({
      response <- httr2::request(url) |>
        httr2::req_timeout(seconds = timeout_seconds) |>
        httr2::req_options(ssl_verifypeer = TRUE, ssl_verifyhost = 2L) |>
        httr2::req_user_agent("R-Shiny-Dashboard/1.0") |>
        httr2::req_perform()

      if (httr2::resp_is_error(response)) {
        status_code <- httr2::resp_status(response)

        # Handle rate limiting (429 Too Many Requests)
        if (status_code == 429L) {
          retry_after <- httr2::resp_header(response, "Retry-After")
          delay <- if (!is.null(retry_after)) as.integer(retry_after) else 60L
          Sys.sleep(delay)
          return(NULL)  # Retry on next iteration
        }

        if (status_code >= 500L && attempt < max_retries) {
          # Server error - retry with backoff
          delay <- retry_base_delay_seconds * (2L ^ (attempt - 1L))
          Sys.sleep(delay)
          return(NULL)
        }
        stop(sprintf("HTTP %d error", status_code))
      }

      content <- httr2::resp_body_string(response)
      parsed <- jsonlite::fromJSON(content, simplifyVector = TRUE)

      locations <- parsed$protocolSection$contactsLocationsModule$locations
      trial_title <- parsed$protocolSection$identificationModule$briefTitle
      status <- parsed$protocolSection$statusModule$overallStatus

      if (is.null(locations) || nrow(locations) == 0L) {
        return(list(success = TRUE, data = NULL))
      }

      facility <- if (!is.null(locations$facility)) {
        locations$facility
      } else {
        NA_character_
      }
      city_val <- if (!is.null(locations$city)) {
        locations$city
      } else {
        NA_character_
      }
      state_val <- if (!is.null(locations$state)) {
        locations$state
      } else {
        NA_character_
      }
      country_val <- if (!is.null(locations$country)) {
        locations$country
      } else {
        NA_character_
      }
      title_val <- if (!is.null(trial_title)) trial_title else NA_character_
      status_val <- if (!is.null(status)) status else NA_character_

      list(
        success = TRUE,
        data = data.frame(
          nct_id = nct_id,
          facility_name = facility,
          city = city_val,
          state = state_val,
          country = country_val,
          trial_title = title_val,
          status = status_val,
          stringsAsFactors = FALSE
        )
      )
    }, error = function(e) {
      if (attempt < max_retries) {
        delay <- retry_base_delay_seconds * (2L ^ (attempt - 1L))
        Sys.sleep(delay)
        return(NULL)
      }
      message(sprintf(
        "Failed to fetch locations for %s after %d attempts: %s",
        nct_id, max_retries, e$message
      ))
      list(success = FALSE, data = NULL)
    })

    if (!is.null(result)) {
      return(result$data)
    }
  }

  NULL
}

# Fetch Locations for Multiple NCT IDs (Parallel)
#
# Fetches location data for multiple NCT IDs using parallel processing with
# rate limiting. Uses future.apply for concurrent requests with controlled
# parallelism to avoid overwhelming the API.
#
# Args:
#   nct_ids: Character vector of NCT IDs.
#   n_workers: Integer. Number of parallel workers (default: 4).
#   chunk_delay_ms: Integer. Delay between chunks in milliseconds.
#
# Returns:
#   Data frame combining all location data, or empty data frame if none found.
fetch_all_trial_locations <- function(
  nct_ids,
  n_workers = min(4L, parallelly::availableCores()),
  chunk_delay_ms = MAP_API_DELAY_MS
) {
  empty_df <- data.frame(
    nct_id = character(),
    facility_name = character(),
    city = character(),
    state = character(),
    country = character(),
    trial_title = character(),
    status = character(),
    stringsAsFactors = FALSE
  )

  if (length(nct_ids) == 0L) {
    return(empty_df)
  }

  n_trials <- length(nct_ids)
  message(sprintf("Fetching locations for %d NCT trials...", n_trials))

  # Set up parallel processing with robust cleanup
  old_plan <- future::plan()
  on.exit({
    tryCatch(
      future::plan(old_plan),
      error = function(e) {
        message(sprintf("Warning: Failed to restore future plan: %s", e$message))
        tryCatch(future::plan(future::sequential), error = function(e2) NULL)
      }
    )
  }, add = TRUE)
  future::plan(future::multisession, workers = n_workers)

  # Process in chunks to allow rate limiting between batches
  chunk_size <- n_workers * 2L
  n_chunks <- ceiling(n_trials / chunk_size)
  all_locations <- vector("list", n_trials)
  failed_count <- 0L

  for (chunk_idx in seq_len(n_chunks)) {
    start_idx <- (chunk_idx - 1L) * chunk_size + 1L
    end_idx <- min(chunk_idx * chunk_size, n_trials)
    chunk_ids <- nct_ids[start_idx:end_idx]

    # Fetch chunk in parallel
    chunk_results <- future.apply::future_lapply(
      chunk_ids,
      fetch_trial_locations,
      future.seed = TRUE
    )

    # Store results
    for (i in seq_along(chunk_results)) {
      result <- chunk_results[[i]]
      if (!is.null(result) && nrow(result) > 0L) {
        all_locations[[start_idx + i - 1L]] <- result
      } else {
        failed_count <- failed_count + 1L
      }
    }

    # Progress update
    message(sprintf("  Processed %d/%d trials", end_idx, n_trials))

    # Rate limiting between chunks (not after last chunk)
    if (chunk_idx < n_chunks) {
      Sys.sleep(chunk_delay_ms / 1000)
    }
  }

  # Report failures if any
  if (failed_count > 0L) {
    message(sprintf(
      "  Warning: %d/%d trials failed to fetch or had no locations",
      failed_count, n_trials
    ))
  }

  # Combine results
  all_locations <- all_locations[!vapply(all_locations, is.null, logical(1L))]

  if (length(all_locations) == 0L) {
    return(empty_df)
  }

  data.table::rbindlist(all_locations, fill = TRUE) |>
    as.data.frame()
}

# =============================================================================
# GEOCODING FUNCTIONS
# =============================================================================

# Geocode Trial Locations
#
# Uses tidygeocoder with OpenStreetMap Nominatim to convert city/country
# to latitude/longitude coordinates.
#
# Args:
#   locations_df: Data frame with city and country columns.
#
# Returns:
#   Data frame with added lat and lon columns.
geocode_locations <- function(locations_df) {
  if (nrow(locations_df) == 0L) {
    locations_df$lat <- numeric()
    locations_df$lon <- numeric()
    return(locations_df)
  }

  # Create unique location strings for geocoding (avoid duplicate API calls)
  locations_df$location_string <- paste(
    locations_df$city,
    locations_df$country,
    sep = ", "
  )

  unique_locations <- unique(locations_df$location_string)
  unique_locations <- unique_locations[
    !is.na(unique_locations) & unique_locations != "NA, NA"
  ]

  message(sprintf("Geocoding %d unique locations...", length(unique_locations)))

  if (length(unique_locations) == 0L) {
    locations_df$lat <- NA_real_
    locations_df$lon <- NA_real_
    return(locations_df)
  }

  # Geocode unique locations
  geocode_df <- data.frame(
    location_string = unique_locations,
    stringsAsFactors = FALSE
  )

  geocoded <- tidygeocoder::geocode(
    geocode_df,
    address = location_string,
    method = "osm",
    quiet = TRUE
  )

  # Merge geocoded coordinates back to original data
  locations_df <- merge(
    locations_df,
    geocoded[, c("location_string", "lat", "long")],
    by = "location_string",
    all.x = TRUE
  )

  # Rename long to lon for consistency
  names(locations_df)[names(locations_df) == "long"] <- "lon"

  # Remove temporary column
  locations_df$location_string <- NULL

  # Log geocoding success rate
  n_geocoded <- sum(!is.na(locations_df$lat))
  message(sprintf(
    "  Geocoded %d/%d locations (%.0f%%)",
    n_geocoded,
    nrow(locations_df),
    100 * n_geocoded / nrow(locations_df)
  ))

  locations_df
}

# =============================================================================
# CACHING FUNCTIONS
# =============================================================================

# Save Cache with Integrity Hash
#
# Saves data to cache file with a separate SHA256 integrity hash file.
# This prevents deserialization attacks via tampered cache files.
#
# Args:
#   data: Data to cache.
#   cache_path: Path to cache file.
#
# Returns:
#   Invisible NULL.
save_cache_with_integrity <- function(data, cache_path) {
  # Save the data
  qs::qsave(data, cache_path, nthreads = parallel::detectCores())

  # Compute hash of the saved FILE (not the R object)
  hash <- digest::digest(file = cache_path, algo = "sha256")
  hash_path <- paste0(cache_path, ".sha256")
  writeLines(hash, hash_path)

  invisible(NULL)
}

# Load Cache with Integrity Verification
#
# Loads cached data only if the integrity hash matches. Returns NULL if
# cache is missing, corrupted, or integrity check fails.
#
# Args:
#   cache_path: Path to cache file.
#
# Returns:
#   Cached data or NULL if verification fails.
load_cache_with_integrity <- function(cache_path) {
  hash_path <- paste0(cache_path, ".sha256")

  # Both files must exist
  if (!file.exists(cache_path) || !file.exists(hash_path)) {
    return(NULL)
  }

  tryCatch({
    # Read stored hash
    stored_hash <- trimws(readLines(hash_path, n = 1L, warn = FALSE))

    # Verify file integrity BEFORE deserialization
    file_hash <- digest::digest(file = cache_path, algo = "sha256")
    if (!identical(file_hash, stored_hash)) {
      warning("Cache integrity check failed - hash mismatch")
      return(NULL)
    }

    # Safe to deserialize after hash verification
    qs::qread(cache_path, nthreads = parallel::detectCores())
  }, error = function(e) {
    warning(sprintf("Cache load error: %s", conditionMessage(e)))
    NULL
  })
}

# Load or Fetch Geocoded Trial Locations
#
# Checks for cached geocoded data. If cache exists and is valid, returns
# cached data. Otherwise, fetches from API, geocodes, and saves to cache.
#
# Args:
#   table2: Data frame containing clinical trial data.
#   force_refresh: Logical. If TRUE, ignores cache and fetches fresh data.
#
# Returns:
#   Data frame with columns: nct_id, facility_name, city, state,
#   country, lat, lon.
load_or_fetch_geocoded_trials <- function(table2, force_refresh = FALSE) {
  cache_path <- MAP_CACHE_PATH

  # Extract current NCT IDs for cache validation
  current_nct_ids <- extract_nct_ids(table2)
  current_hash <- digest::digest(sort(current_nct_ids))

  # Check for existing cache with integrity verification
  if (!force_refresh) {
    cached_data <- load_cache_with_integrity(cache_path)

    if (!is.null(cached_data)) {
      # Validate cache by checking if NCT IDs match
      if (!is.null(cached_data$hash) && cached_data$hash == current_hash) {
        message("Using cached geocoded trial locations.")
        return(cached_data$locations)
      }

      message("Cache invalidated (NCT IDs changed), fetching fresh data...")
    }
  }

  # Fetch and geocode
  locations <- fetch_all_trial_locations(current_nct_ids)

  if (nrow(locations) > 0L) {
    locations <- geocode_locations(locations)
  }

  # Save to cache with integrity hash
  tryCatch({
    cache_data <- list(
      locations = locations,
      hash = current_hash,
      timestamp = Sys.time()
    )

    # Ensure directory exists
    cache_dir <- dirname(cache_path)
    if (!dir.exists(cache_dir)) {
      dir.create(cache_dir, recursive = TRUE, mode = "0700")
    }

    save_cache_with_integrity(cache_data, cache_path)
    message(sprintf("Cached geocoded locations to %s", cache_path))
  }, error = function(e) {
    warning(sprintf("Failed to save cache: %s", conditionMessage(e)))
  })

  locations
}

# Get CSS Class for Status Badge
#
# Returns the appropriate CSS class based on trial recruitment status.
#
# Args:
#   status: Character. The trial recruitment status.
#
# Returns:
#   Character. CSS class name for the status badge.
get_status_class <- function(status) {
  if (is.na(status)) return("popup-status-unknown")
  status_upper <- toupper(status)
  recruiting_statuses <- c("RECRUITING", "ENROLLING_BY_INVITATION")
  active_statuses <- c("ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING")
  terminated_statuses <- c("TERMINATED", "WITHDRAWN", "SUSPENDED")

  if (status_upper %in% recruiting_statuses) {
    "popup-status-recruiting"
  } else if (status_upper %in% active_statuses) {
    "popup-status-active"
  } else if (status_upper == "COMPLETED") {
    "popup-status-completed"
  } else if (status_upper %in% terminated_statuses) {
    "popup-status-terminated"
  } else {
    "popup-status-unknown"
  }
}

# Format Status Display Text
#
# Converts API status values to human-readable display text.
#
# Args:
#   status: Character. The trial recruitment status from API.
#
# Returns:
#   Character. Formatted status text for display.
format_status_text <- function(status) {
  if (is.na(status)) return("Unknown")
  status_map <- c(
    "RECRUITING" = "Recruiting",
    "ENROLLING_BY_INVITATION" = "Enrolling by Invitation",
    "ACTIVE_NOT_RECRUITING" = "Active, Not Recruiting",
    "NOT_YET_RECRUITING" = "Not Yet Recruiting",
    "COMPLETED" = "Completed",
    "TERMINATED" = "Terminated",
    "WITHDRAWN" = "Withdrawn",
    "SUSPENDED" = "Suspended",
    "UNKNOWN" = "Unknown"
  )
  status_upper <- toupper(status)
  if (status_upper %in% names(status_map)) {
    status_map[[status_upper]]
  } else {
    status
  }
}

# Jitter Duplicate Coordinates
#
# Arranges markers that share identical coordinates in a circular pattern.
# This prevents markers from stacking on top of each other and ensures
# each marker is individually clickable with adequate spacing.
#
# Args:
#   map_data: Data frame with lat and lon columns.
#   radius: Numeric. Radius of circle in degrees (~330m at equator).
#
# Returns:
#   Data frame with jittered coordinates for duplicates.
jitter_duplicate_coordinates <- function(map_data, radius = 0.003) {
  if (nrow(map_data) <= 1L) {
    return(map_data)
  }

  # Create coordinate key for grouping
  coord_key <- paste(map_data$lat, map_data$lon, sep = "_")

  # Find duplicates
  coord_counts <- table(coord_key)
  duplicate_keys <- names(coord_counts[coord_counts > 1L])

  if (length(duplicate_keys) == 0L) {
    return(map_data)
  }


  # Process each duplicate location
 for (key in duplicate_keys) {
    indices <- which(coord_key == key)
    n <- length(indices)

    if (n == 1L) next

    # Keep first marker at center, arrange others in circle
    for (i in seq_along(indices)[-1L]) {
      # Position around circle (skip position 0 which is center)
      angle <- 2 * pi * (i - 1L) / (n - 1L)
      map_data$lat[indices[i]] <- map_data$lat[indices[i]] +
        radius * sin(angle)
      map_data$lon[indices[i]] <- map_data$lon[indices[i]] +
        radius * cos(angle)
    }
  }

  map_data
}

# Prepare Map Data for Leaflet
#
# Filters geocoded locations to remove missing coordinates, joins with table2
# metadata, and prepares popup content for map markers using vectorized
# operations for performance.
#
# Args:
#   locations_df: Data frame with geocoded trial locations.
#   table2: Data frame with trial metadata for popup content.
#
# Returns:
#   Data frame ready for leaflet rendering with popup_content column.
prepare_map_data <- function(locations_df, table2) {
  empty_df <- data.frame(
    lat = numeric(),
    lon = numeric(),
    popup_content = character(),
    stringsAsFactors = FALSE
  )

  if (is.null(locations_df) || nrow(locations_df) == 0L) {
    return(empty_df)
  }

  # Filter out rows with missing coordinates
  has_coords <- !is.na(locations_df$lat) & !is.na(locations_df$lon)
  map_data <- locations_df[has_coords, ]

  if (nrow(map_data) == 0L) {
    return(empty_df)
  }

  # Deduplicate locations: one marker per unique trial+facility+city+country
  map_data <- as.data.table(map_data)
  dedup_cols <- c("nct_id", "facility_name", "city", "country")
  dedup_cols <- dedup_cols[dedup_cols %in% names(map_data)]
  map_data <- unique(map_data, by = dedup_cols)
  map_data <- as.data.frame(map_data)

  # Jitter duplicate coordinates to prevent marker stacking
  # Markers at identical lat/lon can't be clicked individually
  map_data <- jitter_duplicate_coordinates(map_data)

  # Join with table2 to get Drug, Phase, Sponsor Type, Sample Size, etc.
  has_table2 <- !is.null(table2) && nrow(table2) > 0L
  has_registry <- "Registry ID" %in% names(table2)
  if (has_table2 && has_registry) {
    cols_to_get <- c(
      "Registry ID",
      if ("Drug" %in% names(table2)) "Drug",
      if ("Clinical Trial Phase" %in% names(table2)) "Clinical Trial Phase",
      if ("Sponsor Type" %in% names(table2)) "Sponsor Type",
      if ("Target Sample Size" %in% names(table2)) "Target Sample Size",
      if ("Estimated Completion Date" %in% names(table2)) {
        "Estimated Completion Date"
      }
    )
    table2_subset <- table2[, ..cols_to_get]

    # Deduplicate table2 by Registry ID to prevent many-to-many join
    reg_id_counts <- table(table2_subset$`Registry ID`)
    if (any(reg_id_counts > 1L)) {
      table2_subset <- table2_subset[,
        lapply(.SD, function(x) {
          if (is.character(x)) {
            paste(unique(na.omit(x)), collapse = ", ")
          } else {
            x[1L]
          }
        }),
        by = `Registry ID`
      ]
    }

    map_data <- merge(
      map_data,
      table2_subset,
      by.x = "nct_id",
      by.y = "Registry ID",
      all.x = TRUE
    )
  }

  # Generate popup content using vectorized operations
  map_data$popup_content <- build_popup_content_vectorized(map_data)

  map_data
}

# Build Popup Content Vectorized
#
# Generates HTML popup content for all map markers using vectorized string
# operations for better performance than row-by-row processing.
#
# Args:
#   map_data: Data frame with map marker data.
#
# Returns:
#   Character vector of HTML popup content.
build_popup_content_vectorized <- function(map_data) {
  n <- nrow(map_data)

  # Helper to safely get column or return NA vector
  safe_col <- function(col_name, default = NA_character_) {
    if (col_name %in% names(map_data)) {
      map_data[[col_name]]
    } else {
      rep(default, n)
    }
  }

  # Extract all columns (vectorized)
  trial_title <- safe_col("trial_title")
  status <- safe_col("status")
  drug <- safe_col("Drug")
  phase <- safe_col("Clinical Trial Phase")
  sponsor_type <- safe_col("Sponsor Type")
  sample_size <- safe_col("Target Sample Size")
  completion_date <- safe_col("Estimated Completion Date")
  facility_name <- safe_col("facility_name")
  city <- safe_col("city")
  state <- safe_col("state")
  country <- safe_col("country")
  nct_id <- safe_col("nct_id")

  # Replace NA facility with default
  facility_name[is.na(facility_name)] <- "Unknown Facility"

  # HTML escape all text fields (vectorized via vapply)
  esc <- function(x) {
    vapply(x, function(v) {
      if (is.na(v)) NA_character_ else htmltools::htmlEscape(as.character(v))
    }, character(1L), USE.NAMES = FALSE)
  }

  trial_title_esc <- esc(trial_title)
  facility_name_esc <- esc(facility_name)
  city_esc <- esc(city)
  state_esc <- esc(state)
  country_esc <- esc(country)
  nct_id_esc <- esc(nct_id)
  drug_esc <- esc(drug)
  phase_esc <- esc(phase)
  sponsor_type_esc <- esc(sponsor_type)
  sample_size_esc <- esc(sample_size)
  completion_date_esc <- esc(completion_date)

  # Get status classes and formatted text (vectorized)
  status_class <- vapply(status, get_status_class, character(1L),
                         USE.NAMES = FALSE)
  status_text <- vapply(status, format_status_text, character(1L),
                        USE.NAMES = FALSE)
  status_text_esc <- esc(status_text)

  # Build state suffix (", STATE" or "")
  state_suffix <- ifelse(
    !is.na(state) & state != "",
    paste0(", ", state_esc),
    ""
  )

  # Build title section
  title_html <- ifelse(
    !is.na(trial_title),
    sprintf("<div class='popup-title'>%s</div>", trial_title_esc),
    ""
  )

  # Build status badge
  status_html <- ifelse(
    !is.na(status),
    sprintf(
      "<div class='popup-status %s'>%s</div>",
      status_class, status_text_esc
    ),
    ""
  )

  # Build divider after title/status
  title_status_divider <- ifelse(
    !is.na(trial_title) | !is.na(status),
    "<div class='popup-divider'></div>",
    ""
  )

  # Build drug row
  drug_row_fmt <- paste0(
    "<div class='popup-info-row'>",
    "<span class='popup-label'>Drug:</span> %s</div>"
  )
  drug_html <- ifelse(
    !is.na(drug),
    sprintf(drug_row_fmt, drug_esc),
    ""
  )

  # Build phase/sponsor row
  phase_part <- ifelse(!is.na(phase), sprintf("Phase: %s", phase_esc), "")
  sponsor_part <- ifelse(
    !is.na(sponsor_type),
    sprintf("Sponsor: %s", sponsor_type_esc),
    ""
  )
  phase_sponsor_combined <- ifelse(
    phase_part != "" & sponsor_part != "",
    paste(phase_part, sponsor_part, sep = " &bull; "),
    paste0(phase_part, sponsor_part)
  )
  phase_sponsor_html <- ifelse(
    phase_sponsor_combined != "",
    sprintf("<div class='popup-info-row'>%s</div>", phase_sponsor_combined),
    ""
  )

  # Build sample size row
  sample_row_fmt <- paste0(
    "<div class='popup-info-row'>",
    "<span class='popup-label'>Sample Size:</span> %s</div>"
  )
  sample_size_html <- ifelse(
    !is.na(sample_size),
    sprintf(sample_row_fmt, sample_size_esc),
    ""
  )

  # Build completion date row
  completion_row_fmt <- paste0(
    "<div class='popup-info-row'>",
    "<span class='popup-label'>Est. Completion:</span> %s</div>"
  )
  completion_html <- ifelse(
    !is.na(completion_date),
    sprintf(completion_row_fmt, completion_date_esc),
    ""
  )

  # Combine trial info section
  trial_info_content <- paste0(
    drug_html, phase_sponsor_html, sample_size_html, completion_html
  )
  trial_info_html <- ifelse(
    trial_info_content != "",
    paste0(
      "<div class='popup-trial-info'>",
      trial_info_content,
      "</div><div class='popup-divider'></div>"
    ),
    ""
  )

  # Build facility and location section
  facility_fmt <- paste0(
    "<div class='popup-facility'>",
    "<span class='popup-facility-icon'>&#128205;</span> %s</div>"
  )
  facility_html <- sprintf(facility_fmt, facility_name_esc)

  location_html <- sprintf(
    "<div class='popup-location'>%s%s, %s</div>",
    ifelse(is.na(city_esc), "", city_esc),
    state_suffix,
    ifelse(is.na(country_esc), "", country_esc)
  )

  # Build ClinicalTrials.gov link
  link_fmt <- paste0(
    "<a href='https://clinicaltrials.gov/study/%s' target='_blank' ",
    "rel='noopener noreferrer' class='popup-link'>",
    "View on ClinicalTrials.gov &rarr;</a>"
  )
  link_html <- sprintf(link_fmt, nct_id_esc)

  # Combine all parts
  popup_fmt <- paste0(
    "<div class='map-popup'>%s%s%s%s%s%s",
    "<div class='popup-divider'></div>%s</div>"
  )
  sprintf(
    popup_fmt,
    title_html,
    status_html,
    title_status_divider,
    trial_info_html,
    facility_html,
    location_html,
    link_html
  )
}

# nolint end: object_usage_linter.
