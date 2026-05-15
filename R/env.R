# env.R
# Environment variable loading and access helpers

# =============================================================================
# .ENV LOADING
# =============================================================================

load_project_env <- function(path = ".env") {
  if (!file.exists(path)) {
    message(sprintf("No .env file at '%s'; using process environment only", path))
    return(invisible(FALSE))
  }

  lines <- trimws(readLines(path, warn = FALSE))
  lines <- lines[nzchar(lines) & !startsWith(lines, "#")]

  for (line in lines) {
    line <- sub("^export[[:space:]]+", "", line)
    if (!grepl("=", line, fixed = TRUE)) next

    key <- trimws(sub("=.*$", "", line))
    value <- trimws(sub("^[^=]*=", "", line))

    if (!grepl("^[A-Za-z_][A-Za-z0-9_]*$", key)) next

    quote_char <- substr(value, 1L, 1L)
    has_matched_quotes <- nchar(value) >= 2L &&
      quote_char %in% c("'", "\"") &&
      substr(value, nchar(value), nchar(value)) == quote_char
    if (has_matched_quotes) {
      value <- substr(value, 2L, nchar(value) - 1L)
    }

    do.call(Sys.setenv, setNames(list(value), key))
  }

  invisible(TRUE)
}

# =============================================================================
# ENVIRONMENT ACCESSORS
# =============================================================================

# Unlike Sys.getenv(name, unset = default), this also falls back to `default`
# when the variable is set to an empty string (e.g., `DB_NAME=` in .env).
env_default <- function(name, default) {
  value <- Sys.getenv(name, unset = NA_character_)
  if (is.na(value) || identical(value, "")) default else value
}

env_int_default <- function(name, default) {
  value <- suppressWarnings(as.integer(env_default(name, as.character(default))))
  if (is.na(value)) default else value
}
