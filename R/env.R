# Environment loading helpers

load_project_env <- function(path = ".env") {
  if (!file.exists(path)) {
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
    if (
      nchar(value) >= 2L &&
        quote_char %in% c("'", "\"") &&
        substr(value, nchar(value), nchar(value)) == quote_char
    ) {
      value <- substr(value, 2L, nchar(value) - 1L)
    }

    do.call(Sys.setenv, setNames(list(value), key))
  }

  invisible(TRUE)
}
