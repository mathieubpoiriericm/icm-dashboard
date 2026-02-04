# ui.R
# UI definition for SVD Dashboard
# nolint start: object_usage_linter.

# =============================================================================
# BSLIB THEME DEFINITIONS
# =============================================================================

# Light Theme
# Bootstrap 5 theme matching the current light appearance.
light_theme <- bslib::bs_theme(
  version = 5,
  bg = "#ffffff",
  fg = "#1f2937",
  primary = "#2d287a",
  secondary = "#667eea",
  success = "#008000",
  danger = "#ff0000",
  # Use local Roboto font (loaded in app.R) instead of font_google() for speed
  base_font = "Roboto, sans-serif",
  heading_font = "Roboto, sans-serif",
  "navbar-bg" = "#f8f9fc",
  "navbar-light-color" = "#1f2937"
)

# Dark Theme with Glassmorphism
# Bootstrap 5 dark theme with glassmorphism support.
dark_theme <- bslib::bs_theme(
  version = 5,
  bg = "#121212",
  fg = "#e0e0e0",
  primary = "#6366f1",
  secondary = "#818cf8",
  success = "#4ade80",
  danger = "#f87171",
  # Use local Roboto font (loaded in app.R) instead of font_google() for speed
  base_font = "Roboto, sans-serif",
  heading_font = "Roboto, sans-serif",
  "navbar-bg" = "#1a1a2e",
  "navbar-dark-color" = "#e0e0e0"
)

# Build UI Function for SVD Dashboard
#
# Creates the UI definition for the Shiny application using bslib for
# Bootstrap 5 theming with light/dark mode support.
#
# Args:
#   n_genes: Integer. Number of unique genes in the Gene Table.
#   n_drugs: Integer. Number of unique drugs in the Clinical Trials Table.
#   n_trials: Integer. Number of unique clinical trials.
#   n_pubs: Integer. Number of unique publications.
#
# Returns:
#   A Shiny UI object.
build_ui <- function(n_genes = 0L, n_drugs = 0L, n_trials = 0L, n_pubs = 0L) {
  bslib::page_navbar(
    id = "tabs",
    # Use WebP with PNG fallback for better performance
    title = shiny::tags$picture(
      shiny::tags$source(srcset = "images/icm_logo.webp", type = "image/webp"),
      shiny::tags$img(
        src = "images/icm_logo.png",
        alt = "ICM Logo",
        height = "40"
      )
    ),
    window_title = "ICM Cerebral SVD Dashboard",
    theme = light_theme,
    fillable = FALSE,
    bg = NULL,

    # Include custom CSS and JavaScript in header
    header = shiny::tagList(
      shiny::tags$head(
        # Favicon
        shiny::tags$link(
          rel = "icon",
          type = "image/png",
          href = "images/icm_logo.png"
        ),

        # External CSS (minified at startup in app.R for faster loading)
        shiny::tags$link(rel = "stylesheet", href = "custom.min.css"),

        # Tippy components (bundled locally for faster loading)
        # Using defer to prevent render-blocking
        shiny::tags$link(rel = "stylesheet", href = "css/tippy.css"),
        shiny::tags$script(src = "js/popper.min.js", defer = NA),
        shiny::tags$script(src = "js/tippy.min.js", defer = NA),

        # External JavaScript (minified for faster loading, defer for
        # non-blocking)
        shiny::tags$script(src = "custom.min.js", defer = NA)
      ),
      # Persistent title section (appears on all tabs)
      shiny::div(
        class = "title-section",
        shiny::h2(paste0(
          "Putative Causal Genes and Clinical Trial Drugs ",
          "for Cerebral Small Vessel Disease (SVD)"
        ))
      )
    ),

    # Header info (centered) and dark mode toggle in navbar
    bslib::nav_spacer(),
    bslib::nav_item(
      shiny::tags$span(
        class = "navbar-text header-info",
        shiny::HTML(sprintf(
          "&copy; %s %s. All rights reserved.",
          format(Sys.Date(), "%Y"),
          "Paris Brain Institute (ICM)"
        )),
        shiny::tags$span(class = "divider mx-2", "|"),
        shiny::tags$a(
          href = "https://opensource.org/licenses/MIT",
          target = "_blank",
          rel = "noopener",
          class = "header-link",
          "MIT License"
        )
      )
    ),
    bslib::nav_spacer(),
    bslib::nav_item(
      shiny::tags$span(
        id = "theme-toggle-btn",
        class = "theme-toggle-btn d-flex align-items-center gap-2",
        bslib::input_dark_mode(id = "dark_mode", mode = "light")
      )
    ),

    # About tab
    bslib::nav_panel(
      title = shiny::tagList(
        shiny::icon("info-circle", class = "me-1"),
        "About"
      ),
      value = "About",
      shiny::div(
        class = "main-container",
        shiny::div(
          class = "about-content",
          shiny::br(),
          shiny::br(),
          shiny::div(
            class = "text-center",
            bslib::card(
              class = "d-inline-block border-danger border-2",
              fill = FALSE,
              bslib::card_body(
                class = "text-danger fw-bold fs-5 py-3 px-4",
                paste0(
                  "This is a preview of a dashboard ",
                  "that is still a work in progress!"
                )
              )
            )
          ),
          shiny::div(
            class = "p-5 text-center",
            shiny::h3(
              class = "about-header",
              # nolint next: line_length_linter.
              "Welcome to the Paris Brain Institute's Cerebral SVD Dashboard!"
            ),
            shiny::p(
              class = "about-text",
              shiny::HTML(paste0(
                "This dashboard provides <b>up-to-date</b> and ",
                # nolint next: line_length_linter.
                "<b>standardized</b> information on putative cerebral SVD causal ",
                # nolint next: line_length_linter.
                "genes<br>and drugs tested in planned or ongoing cerebral SVD ",
                "clinical trials."
              ))
            ),
            shiny::br(),
            shiny::br(),
            shiny::br(),
            shiny::div(
              class = "date-label",
              "Up-to-date as of:",
              shiny::span(
                class = "date-badge",
                format(Sys.Date(), "%B %d, %Y")
              ),
            ),
            shiny::tags$hr(class = "section-divider"),
            bslib::layout_columns(
              col_widths = c(2, 1, 2, 1, 2, 2, 2),
              fill = FALSE,
              class = "align-items-center justify-content-center my-3",
              # Genes value box
              bslib::value_box(
                title = "Putative Causal Genes",
                value = n_genes,
                showcase = NULL
              ),
              # Connector between Genes and Drugs
              shiny::span(class = "fs-4 fw-bold text-body", "&"),
              # Drugs value box
              bslib::value_box(
                title = "Drugs Tested",
                value = n_drugs,
                showcase = NULL
              ),
              # Connector between Drugs and Trials
              shiny::span(class = "fs-4 fw-bold text-body", "in"),
              # Trials value box
              bslib::value_box(
                title = "Clinical Trials",
                value = n_trials,
                showcase = NULL
              ),
              # Connector between Trials and Publications
              shiny::span(class = "fs-4 fw-bold text-body", "based on"),
              # Publications value box
              bslib::value_box(
                title = "Peer-Reviewed Publications",
                value = n_pubs,
                showcase = NULL
              )
            ),
            shiny::tags$hr(class = "section-divider"),
            shiny::br(),
            shiny::br(),
            about_info_card(
              "How to Cite:",
              shiny::span(
                class = "text-body",
                shiny::HTML(paste0(
                  " Last Name, Initial. ",
                  "<i>et al.</i> Publication Title. <i>Journal.</i> ",
                  "(Publication Year) DOI"
                ))
              )
            ),
            about_info_card("Scientific Board:", "TBD"),
            about_info_card("Contact Us:", "TBD"),
            about_info_card(
              "Maintenance:",
              shiny::tags$a(
                href = "mailto:mathieu.poirier@icm-institute.org",
                class = "text-primary text-decoration-none",
                "mathieu.poirier@icm-institute.org"
              )
            ),
            about_info_card("Acknowledgements:", "TBD")
          )
        )
      )
    ),

    # Gene Table tab with sidebar
    bslib::nav_panel(
      title = shiny::tagList(shiny::icon("dna", class = "me-1"), "Genes"),
      value = "Gene Table",
      bslib::layout_sidebar(
        sidebar = bslib::sidebar(
          width = "auto",
          class = "sidebar-section",
          checkbox_filter_ui(
            "mr_filter",
            "Mendelian Randomization Performed?",
            choices = c(Yes = "Yes", No = "No"),
            selected = c("Yes", "No")
          ),
          checkbox_filter_ui(
            "gwas_trait_filter",
            "GWAS Traits",
            choices = c(
              "Show All" = "all",
              "None Found" = "(none found)",
              SVS = "SVS",
              "BG-PVS" = "BG-PVS",
              WMH = "WMH",
              "HIP-PVS" = "HIP-PVS",
              PSMD = "PSMD",
              "Extreme-cSVD" = "extreme-cSVD",
              Lacunes = "lacunes",
              Stroke = "stroke",
              NODDI = "NODDI",
              FA = "FA",
              MD = "MD",
              "Lacunar Stroke" = "lacunar stroke",
              "Small Vessel Stroke" = "small vessel stroke"
            ),
            selected = "all"
          ),
          checkbox_filter_ui(
            "omics_filter",
            "Evidence From Other Omics Studies",
            choices = c(
              "Show All" = "all",
              "None Found" = "(none found)",
              EWAS = "EWAS",
              TWAS = "TWAS",
              PWAS = "PWAS",
              Proteomics = "proteomics",
              MENTR = "mutation effect prediction on ncRNA transcription"
            ),
            selected = "all"
          )
        ),
        # Main content
        shiny::div(
          class = "main-content",
          shiny::div(
            class = "content-title",
            shiny::HTML(paste0(
              "<strong>Putative Causal Genes for ",
              "Cerebral Small Vessel Disease</strong>"
            ))
          ),
          shiny::br(),
          tip_row_ui(
            tip_box_ui(paste0(
              "Elements with a grey background like this have tooltips. ",
              "Hover over them to see additional information."
            )),
            tip_box_ui(paste0(
              "Elements with a <span class=\"link-hint\">blue font</span> ",
              "are clickable links that open in a new tab."
            )),
            tip_box_ui(paste0(
              "Definitions for affected pathways are according to ",
              "<a href=\"https://geneontology.org/docs/",
              "ontology-documentation/\" target=\"_blank\" ",
              "rel=\"noopener\" class=\"tip-link\">GO terminology</a>"
            ))
          ),
          shiny::uiOutput("filter_message_table1"),
          # bslib table controls
          shiny::div(
            class = "dt-bslib-controls",
            shiny::div(
              class = "dt-bslib-control-group",
              shiny::tags$label(class = "dt-bslib-label", "Show"),
              shiny::selectInput(
                "table1_page_length",
                label = NULL,
                choices = c(10L, 25L, 50L, 100L),
                selected = 10L,
                width = "80px"
              ),
              shiny::tags$label(class = "dt-bslib-label", "entries")
            ),
            shiny::div(
              class = "dt-bslib-control-group",
              shiny::tags$label(class = "dt-bslib-label", "Search:"),
              shiny::textInput(
                "table1_search",
                label = NULL,
                width = "200px"
              )
            )
          ),
          DT::dataTableOutput("firstTable")
        )
      )
    ),

    # Phenogram tab (no sidebar)
    bslib::nav_panel(
      title = shiny::tagList(
        shiny::icon("project-diagram", class = "me-1"),
        "Phenogram"
      ),
      value = "Phenogram",
      shiny::div(
        class = "main-content phenogram-content",
        shiny::br(),
        shiny::div(
          class = "content-title",
          shiny::HTML("<strong>Interactive Phenogram</strong>")
        ),
        tip_row_ui(
          tip_box_ui(paste0(
            "Hover over coloured elements ",
            "(GWAS phenotypes) to view more information."
          )),
          centered = TRUE
        ),
        shiny::tags$iframe(
          src = "phenogram_template.html",
          class = "viz-iframe phenogram-iframe",
          loading = "lazy",
          sandbox = "allow-scripts allow-same-origin"
        )
      )
    ),

    # Clinical Trials Table tab with sidebar
    bslib::nav_panel(
      title = shiny::tagList(
        shiny::icon("flask", class = "me-1"),
        "Clinical Trials"
      ),
      value = "Clinical Trials Table",
      bslib::layout_sidebar(
        sidebar = bslib::sidebar(
          width = "auto",
          class = "sidebar-section",
          checkbox_filter_ui(
            "ge_filter",
            "Genetic Evidence?",
            choices = c(Yes = "Yes", No = "No"),
            selected = c("Yes", "No")
          ),
          checkbox_filter_ui(
            "reg_filter",
            "Clinical Trial Registry",
            choices = c(
              "Show All" = "all",
              "ClinicalTrials.gov (NCT)" = "NCT",
              # nolint next: line_length_linter.
              "International Standard Randomised\nControlled Trial Number (ISRCTN)" = "ISRCTN",
              # nolint next: line_length_linter.
              "Australian New Zealand Clinical\nTrials Registry (ANZCTR)" = "ACTRN",
              "Chinese Clinical Trial Register (ChiCTR)" = "ChiCTR"
            ),
            selected = "all"
          ),
          checkbox_filter_ui(
            "ct_filter",
            "Clinical Trial Phase",
            choices = c(
              "Show All" = "all",
              "Clinical Trial Phase I" = "I",
              "Clinical Trial Phase II" = "II",
              "Clinical Trial Phase III" = "III"
            ),
            selected = "all"
          ),
          checkbox_filter_ui(
            "pop_filter",
            "SVD Population",
            choices = c(
              "Show All" = "all",
              CAA = "CAA",
              "Cognitive Impairment" = "Cognitive Impairment",
              Stroke = "Stroke",
              SVD = "SVD"
            ),
            selected = "all"
          ),
          bslib::card(
            id = "sample_size_filter_card",
            fill = FALSE,
            class = "mb-3",
            bslib::card_body(
              class = "py-3 px-3",
              shiny::strong("Target Sample Size"),
              shiny::plotOutput("sample_size_histogram", height = "180px"),
              shiny::sliderInput(
                "sample_size_filter",
                NULL,
                min = SAMPLE_SIZE_MIN,
                max = SAMPLE_SIZE_MAX,
                value = c(SAMPLE_SIZE_MIN, SAMPLE_SIZE_MAX),
                step = 1L
              )
            )
          ),
          checkbox_filter_ui(
            "spon_filter",
            "Sponsor Type",
            choices = c(
              "Show All" = "all",
              Academic = "Academic",
              Industry = "Industry"
            ),
            selected = "all"
          )
        ),
        # Main content
        shiny::div(
          class = "main-content",
          shiny::div(
            class = "content-title",
            shiny::HTML(paste0(
              "<strong>Drugs Used in Planned or Ongoing ",
              "Clinical Trials for Cerebral Small Vessel ",
              "Disease</strong>"
            ))
          ),
          shiny::br(),
          tip_row_ui(
            tip_box_ui(paste0(
              "Elements with a grey background like this have tooltips. ",
              "Hover over them to see additional information."
            )),
            tip_box_ui(paste0(
              "Elements with a <span class=\"link-hint\">blue font</span> ",
              "are clickable links that open in a new tab."
            )),
            tip_box_ui(paste0(
              "Definitions for genetic targets and genetic ",
              "evidence are according to ",
              "<a href=\"https://platform.opentargets.org/\" ",
              "target=\"_blank\" rel=\"noopener\" class=\"tip-link\">",
              "Open Targets</a>"
            ))
          ),
          shiny::uiOutput("filter_message_table2"),
          # bslib table controls
          shiny::div(
            class = "dt-bslib-controls",
            shiny::div(
              class = "dt-bslib-control-group",
              shiny::tags$label(class = "dt-bslib-label", "Show"),
              shiny::selectInput(
                "table2_page_length",
                label = NULL,
                choices = c(10L, 25L, 50L, 100L),
                selected = 10L,
                width = "80px"
              ),
              shiny::tags$label(class = "dt-bslib-label", "entries")
            ),
            shiny::div(
              class = "dt-bslib-control-group",
              shiny::tags$label(class = "dt-bslib-label", "Search:"),
              shiny::textInput(
                "table2_search",
                label = NULL,
                width = "200px"
              )
            )
          ),
          DT::dataTableOutput("secondTable")
        )
      )
    ),

    # Clinical Trials Visualization tab (no sidebar)
    bslib::nav_panel(
      title = shiny::tagList(
        shiny::icon("chart-line", class = "me-1"),
        "Trials Timeline"
      ),
      value = "Clinical Trials Visualization",
      shiny::div(
        class = "main-content viz-content",
        shiny::br(),
        shiny::div(
          class = "content-title",
          shiny::HTML(paste0(
            "<strong>Cerebral SVD Drugs Tested in Planned or ",
            "Ongoing Clinical Trials</strong>"
          ))
        ),
        tip_row_ui(
          tip_box_ui(paste0(
            "Hover over plot elements to display tooltips,<br>",
            "or click on a drug name or drug marker to display sidepanel."
          )),
          tip_box_ui(
            paste0(
              "Fig. 1 in Cummings, J. <i>et al.</i>,<br>",
              "Alzheimer's disease drug development pipeline: 2023,<br>",
              "<i>Alzheimer's Dement.</i> (May 2023) ",
              "<a href=\"https://pubmed.ncbi.nlm.nih.gov/37251912/\" ",
              "target=\"_blank\" rel=\"noopener\" class=\"tip-link\">",
              "DOI: 10.1002/trc2.12385</a>"
            ),
            tip_label = "Visually-inspired by:"
          ),
          centered = TRUE
        ),
        shiny::tags$div(
          class = "iframe-container",
          shiny::tags$iframe(
            src = "python_plot.html",
            class = "viz-iframe trials-iframe",
            loading = "lazy",
            sandbox = "allow-scripts allow-same-origin"
          )
        )
      )
    ),

    # Clinical Trials Map tab (no sidebar)
    bslib::nav_panel(
      title = shiny::tagList(
        shiny::icon("globe", class = "me-1"),
        "Trials Map"
      ),
      value = "Clinical Trials Map",
      shiny::div(
        class = "main-content map-content",
        shiny::br(),
        shiny::div(
          class = "content-title",
          shiny::HTML(paste0(
            "<strong>Location of Cerebral SVD Clinical Trials</strong>"
          ))
        ),
        tip_row_ui(
          tip_box_ui(
            paste0(
              "This map shows research sites from trials registered on ",
              "<strong>ClinicalTrials.gov (NCT IDs only)</strong>.<br>",
              "Trials from other registries (ISRCTN, ACTRN, ChiCTR) ",
              "are not displayed."
            ),
            tip_label = "Note:"
          ),
          tip_box_ui(paste0(
            "Click on clusters to zoom in and reveal individual sites.<br>",
            "Click on markers to view facility details and ",
            "links to ClinicalTrials.gov."
          )),
          centered = TRUE
        ),
        shiny::uiOutput("map_stats"),
        shiny::div(
          class = "map-container",
          leaflet::leafletOutput(
            "trials_map",
            height = paste0(MAP_HEIGHT_PX, "px")
          )
        )
      )
    )
  )
}
# nolint end: object_usage_linter.
