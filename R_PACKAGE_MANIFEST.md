# R Package Manifest

This manifest is derived only from the repository's `.R` source files. It
includes packages found in explicit `library()`, `require()`,
`requireNamespace()`, and `loadNamespace()` calls, plus namespace calls such as
`pkg::function()`.

Comments, README content, lockfiles, and other non-source documentation are not
used as inputs.

## Installable Packages

```r
install.packages(c(
  "bslib",
  "cachem",
  "data.table",
  "DBI",
  "digest",
  "dplyr",
  "DT",
  "fastmap",
  "future",
  "future.apply",
  "ggplot2",
  "ggrepel",
  "ggtext",
  "htmltools",
  "httr2",
  "jsonlite",
  "leaflet",
  "memoise",
  "parallelly",
  "patchwork",
  "purrr",
  "qs",
  "ragg",
  "readr",
  "RPostgres",
  "scales",
  "shiny",
  "shinytest2",
  "shinyWidgets",
  "showtext",
  "stringr",
  "sysfonts",
  "systemfonts",
  "testthat",
  "tidygeocoder",
  "tidyr"
))
```

## R Distribution Packages

These are referenced by the source files but ship with R.

```r
c(
  "parallel",
  "stats",
  "tools",
  "utils"
)
```

## Source References

| Package | Source files |
| --- | --- |
| `bslib` | `app.R`, `R/mod_checkbox_filter.R`, `R/server.R`, `R/ui.R`, `R/utils.R` |
| `cachem` | `app.R`, `R/tooltips.R` |
| `data.table` | `app.R`, `R/data_prep.R`, `R/fetch_trial_locations.R`, `R/filter_utils.R`, `R/tooltips.R`, `tests/test_all.R` |
| `DBI` | `R/clean_table1.R`, `R/clean_table2.R`, `R/read_external_data.R`, `R/utils.R`, `scripts/trigger_update.R` |
| `digest` | `app.R`, `R/data_prep.R`, `R/fetch_trial_locations.R`, `R/utils.R` |
| `dplyr` | `app.R`, `R/clean_table1.R`, `R/data_prep.R`, `scripts/plot_tuning_runs.R` |
| `DT` | `app.R`, `R/server_table1.R`, `R/server_table2.R`, `R/ui.R` |
| `fastmap` | `app.R`, `R/data_prep.R`, `tests/test_all.R` |
| `future` | `R/fetch_trial_locations.R` |
| `future.apply` | `R/fetch_trial_locations.R` |
| `ggplot2` | `scripts/plot_tuning_runs.R` |
| `ggrepel` | `scripts/plot_tuning_runs.R` |
| `ggtext` | `scripts/plot_tuning_runs.R` |
| `htmltools` | `app.R`, `R/fetch_trial_locations.R`, `R/server_table1.R`, `R/tooltips.R` |
| `httr2` | `R/fetch_trial_locations.R` |
| `jsonlite` | `app.R`, `R/fetch_trial_locations.R`, `R/server.R` |
| `leaflet` | `app.R`, `R/server_map.R`, `R/ui.R` |
| `memoise` | `app.R`, `R/tooltips.R` |
| `parallel` | `app.R`, `R/constants.R` |
| `parallelly` | `R/fetch_trial_locations.R` |
| `patchwork` | `scripts/plot_tuning_runs.R` |
| `purrr` | `app.R`, `R/tooltips.R` |
| `qs` | `app.R`, `R/data_prep.R`, `R/fetch_trial_locations.R`, `scripts/trigger_update.R` |
| `ragg` | `scripts/plot_tuning_runs.R` |
| `readr` | `scripts/plot_tuning_runs.R` |
| `RPostgres` | `R/utils.R` |
| `scales` | `scripts/plot_tuning_runs.R` |
| `shiny` | `app.R`, `R/filter_utils.R`, `R/mod_checkbox_filter.R`, `R/server_map.R`, `R/server_table1.R`, `R/server_table2.R`, `R/server.R`, `R/tooltips.R`, `R/ui.R`, `R/utils.R`, `tests/test_all.R` |
| `shinytest2` | `tests/test_all.R` |
| `shinyWidgets` | `app.R`, `R/mod_checkbox_filter.R` |
| `showtext` | `app.R` |
| `stats` | `R/tooltips.R` |
| `stringr` | `app.R`, `R/clean_table1.R`, `R/tooltips.R` |
| `sysfonts` | `app.R` |
| `systemfonts` | `scripts/plot_tuning_runs.R` |
| `testthat` | `tests/test_all.R` |
| `tidygeocoder` | `app.R`, `R/fetch_trial_locations.R` |
| `tidyr` | `scripts/plot_tuning_runs.R` |
| `tools` | `app.R`, `R/data_prep.R`, `R/tooltips.R`, `R/utils.R` |
| `utils` | `R/fetch_trial_locations.R` |
