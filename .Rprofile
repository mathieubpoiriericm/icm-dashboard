source("renv/activate.R")

# vscode-R session watcher workaround for R >= 4.6.0 (vscode-R#1696):
# R 4.6.0 no longer calls a global-env .First.sys override, so the watcher
# never defines .vsc.attach. Re-trigger it via .First(), which R still
# calls. Remove after upgrading to vscode-R 3.0.0.
if (interactive() && Sys.getenv("TERM_PROGRAM") == "vscode") {
  .First <- function() {
    fs <- get0(".First.sys", envir = globalenv(), inherits = FALSE)
    if (is.function(fs)) fs()
  }
}
