# ui.R
# UI definition for SVD Dashboard
# nolint start: object_usage_linter.

#' Build UI Function for SVD Dashboard
#'
#' Creates the UI definition for the Shiny application.
#'
#' @param n_genes Integer. Number of unique genes in the Gene Table.
#' @param n_drugs Integer. Number of unique drugs in the Clinical Trials Table.
#' @param n_trials Integer. Number of unique clinical trials.
#' @param n_pubs Integer. Number of unique publications.
#'
#' @return A Shiny UI object.
#'
#' @export
build_ui <- function(n_genes = 0L, n_drugs = 0L, n_trials = 0L, n_pubs = 0L) {
  shiny::fluidPage(
    shiny::tags$head(
      # External CSS
      shiny::tags$link(rel = "stylesheet", href = "custom.css"),

      # Tippy components (bundled locally for faster loading)
      shiny::tags$link(rel = "stylesheet", href = "css/tippy.css"),
      shiny::tags$script(src = "js/popper.min.js"),
      shiny::tags$script(src = "js/tippy.min.js"),

      # External JavaScript
      shiny::tags$script(src = "custom.js")
    ),

    shiny::tags$body(
      shiny::tags$header(
        class = "app-header",
        role = "banner",
        shiny::tags$div(
          class = "header-inner",
          shiny::HTML(paste0(
            "<img src = \"images/icm_logo.png\" ",
            "alt = \"ICM Logo\" width = \"200\">"
          )),
          shiny::tags$span(class = "divider", "|"),
          shiny::HTML(sprintf(
            "&copy; %s %s. All rights reserved.",
            format(Sys.Date(), "%Y"),
            "Paris Brain Institute (ICM)"
          )),
          shiny::tags$span(class = "divider", "|"),
          shiny::tags$a(
            href = "https://opensource.org/licenses/MIT",
            target = "_blank",
            rel = "noopener",
            "MIT License"
          )
        )
      ),
      shiny::div(
        class = "main-container",
        shiny::div(
          class = "title-section",
          shiny::h2(paste0(
            "Putative Causal Genes and Clinical Trial Drugs ",
            "for Cerebral Small Vessel Disease (SVD)"
          ))
        ),
        shiny::div(
          class = "content-wrapper",

          # Gene Table sidebar filters
          shiny::conditionalPanel(
            condition = "input.tabs == 'Gene Table'",
            shiny::div(
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
            )
          ),

          # Clinical Trials sidebar filters
          shiny::conditionalPanel(
            condition = "input.tabs == 'Clinical Trials Table'",
            shiny::div(
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
              shiny::div(class = "filter-label"),
              shiny::div(
                id = "sample_size_filter_box",
                style = paste0(
                  "background: white; padding: 1.25rem; ",
                  "border-radius: 20px; border: 1px solid #e1e4e8; ",
                  "margin-bottom: 1.5rem; ",
                  "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);"
                ),
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
            )
          ),

          # Main content area with tabs
          shiny::div(
            class = "main-content",
            shiny::tabsetPanel(
              id = "tabs",
              type = "tabs",

              # About tab
              shiny::tabPanel(
                "About",
                shiny::br(),
                shiny::div(
                  style = "text-align: center;",
                  shiny::tags$span(
                    style = warning_box_style,
                    paste0(
                      "This is a preview of a dashboard ",
                      "that is still a work in progress!"
                    )
                  )
                ),
                shiny::div(
                  style = "padding: 4rem; text-align: center;",
                  shiny::h3(
                    style = about_header_style,
                    # nolint next: line_length_linter.
                    "Welcome to the Paris Brain Institute's Cerebral SVD Dashboard!"
                  ),
                  shiny::p(
                    style = about_text_style,
                    shiny::HTML(paste0(
                      "This dashboard provides <b>up-to-date</b> and ",
                      # nolint next: line_length_linter.
                      "<b>standardized</b> information on putative cerebral SVD causal ",
                      # nolint next: line_length_linter.
                      "genes<br>and drugs tested in planned or ongoing cerebral SVD ",
                      "clinical trials."
                    ))
                  ),
                  br(),
                  br(),
                  br(),
                  shiny::div(
                    style = paste0(
                      "text-align: center; margin-top: 1rem; ",
                      "font-size: 1.25rem; color: #4b5563;"
                    ),
                    "As of ",
                    shiny::span(
                      style = paste0(
                        "display: inline-block; background-color: #f3f4f6; ",
                        "padding: 0.25rem 0.75rem; border-radius: 12px; ",
                        "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);"
                      ),
                      format(Sys.Date(), "%B %d, %Y")
                    ),
                    ":"
                  ),
                  shiny::tags$hr(
                    style = paste0(
                      "border: none; border-top: 1px solid #e1e4e8; ",
                      "margin: 1.5rem 0;"
                    )
                  ),
                  shiny::div(
                    style = paste0(
                      "display: flex; justify-content: center; ",
                      "flex-wrap: nowrap; ",
                      "gap: 2rem; margin-top: 1rem; margin-bottom: 1rem;"
                    ),
                    shiny::div(
                      style = paste0(
                        "display: inline-flex; padding: 1.5rem 3rem; ",
                        "background-color: #f8f9fc; border-radius: 20px; ",
                        "border: 1px solid #e1e4e8; ",
                        "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);"
                      ),
                      shiny::div(
                        style = "text-align: center;",
                        shiny::div(
                          style = paste0(
                            "font-size: 2.5rem; font-weight: 700; ",
                            "color: #2d287a;"
                          ),
                          n_genes
                        ),
                        shiny::div(
                          style = paste0(
                            "font-size: 1rem; color: #4b5563; ",
                            "background-color: #e8eaf6; padding: 0.5rem 1rem; ",
                            "border-radius: 12px; margin-top: 0.5rem; ",
                            "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); ",
                            "white-space: nowrap;"
                          ),
                          "Putative Causal Genes"
                        )
                      )
                    ),
                    shiny::div(
                      style = paste0(
                        "display: flex; align-items: center; ",
                        "font-size: 1.5rem; font-weight: 500; color: #4b5563; ",
                        "white-space: nowrap;"
                      ),
                      "&"
                    ),
                    shiny::div(
                      style = paste0(
                        "display: inline-flex; gap: 3rem; ",
                        "padding: 1.5rem 3rem; ",
                        "background-color: #f8f9fc; border-radius: 20px; ",
                        "border: 1px solid #e1e4e8; ",
                        "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);"
                      ),
                      shiny::div(
                        style = "text-align: center;",
                        shiny::div(
                          style = paste0(
                            "font-size: 2.5rem; font-weight: 700; ",
                            "color: #2d287a;"
                          ),
                          n_drugs
                        ),
                        shiny::div(
                          style = paste0(
                            "font-size: 1rem; color: #4b5563; ",
                            "background-color: #e8eaf6; padding: 0.5rem 1rem; ",
                            "border-radius: 12px; margin-top: 0.5rem; ",
                            "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); ",
                            "white-space: nowrap;"
                          ),
                          "Drugs Tested or Being Tested"
                        )
                      ),
                      shiny::div(
                        style = paste0(
                          "display: flex; align-items: center; ",
                          "font-size: 1.5rem; font-weight: 500; ",
                          "color: #4b5563; white-space: nowrap;"
                        ),
                        "in"
                      ),
                      shiny::div(
                        style = "text-align: center;",
                        shiny::div(
                          style = paste0(
                            "font-size: 2.5rem; font-weight: 700; ",
                            "color: #2d287a;"
                          ),
                          n_trials
                        ),
                        shiny::div(
                          style = paste0(
                            "font-size: 1rem; color: #4b5563; ",
                            "background-color: #e8eaf6; padding: 0.5rem 1rem; ",
                            "border-radius: 12px; margin-top: 0.5rem; ",
                            "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); ",
                            "white-space: nowrap;"
                          ),
                          "Planned or Ongoing Clinical Trials"
                        )
                      )
                    ),
                    shiny::div(
                      style = paste0(
                        "display: flex; align-items: center; ",
                        "font-size: 1.5rem; font-weight: 500; color: #4b5563; ",
                        "font-style: italic; white-space: nowrap;"
                      ),
                      "...based on a total of..."
                    ),
                    shiny::div(
                      style = paste0(
                        "display: inline-flex; padding: 1.5rem 3rem; ",
                        "background-color: #f8f9fc; border-radius: 20px; ",
                        "border: 1px solid #e1e4e8; ",
                        "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);"
                      ),
                      shiny::div(
                        style = "text-align: center;",
                        shiny::div(
                          style = paste0(
                            "font-size: 2.5rem; font-weight: 700; ",
                            "color: #2d287a;"
                          ),
                          n_pubs
                        ),
                        shiny::div(
                          style = paste0(
                            "font-size: 1rem; color: #4b5563; ",
                            "background-color: #e8eaf6; padding: 0.5rem 1rem; ",
                            "border-radius: 12px; margin-top: 0.5rem; ",
                            "box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); ",
                            "white-space: nowrap;"
                          ),
                          "Peer-Reviewed Publications"
                        )
                      )
                    )
                  ),
                  shiny::tags$hr(
                    style = paste0(
                      "border: none; border-top: 1px solid #e1e4e8; ",
                      "margin: 1.5rem 0;"
                    )
                  ),
                  br(),
                  br(),
                  shiny::div(
                    style = paste0(
                      "text-align: left; margin-top: 1rem; ",
                      "font-size: 1.5rem; line-height: 1.6;"
                    ),
                    shiny::tags$span(
                      style = about_box_style,
                      shiny::HTML("<b>How to Cite:</b>")
                    ),
                    " ",
                    shiny::tags$span(
                      style = "color: #4b5563;",
                      shiny::HTML(paste0(
                        " Last Name, Initial. ",
                        "<i>et al.</i> Publication Title. <i>Journal.</i> ",
                        "(Publication Year) DOI"
                      ))
                    )
                  ),
                  shiny::div(
                    style = paste0(
                      "text-align: left; margin-top: 1rem; ",
                      "font-size: 1.5rem; line-height: 1.6;"
                    ),
                    shiny::tags$span(
                      style = about_box_style,
                      shiny::HTML("<b>Scientific Board:</b>")
                    ),
                    " ",
                    shiny::tags$span(
                      style = "color: #4b5563;",
                      shiny::HTML(paste0(" TBD"))
                    )
                  ),
                  shiny::div(
                    style = paste0(
                      "text-align: left; margin-top: 1rem; ",
                      "font-size: 1.5rem; color: #2d287a; line-height: 1.6;"
                    ),
                    shiny::tags$span(
                      style = about_box_style,
                      shiny::HTML("<b>Contact Us:</b>")
                    ),
                    " ",
                    shiny::tags$span(
                      style = "color: #4b5563;",
                      shiny::HTML(paste0(
                        "TBD"
                      ))
                    )
                  ),
                  shiny::div(
                    style = paste0(
                      "text-align: left; margin-top: 1rem; ",
                      "font-size: 1.5rem; color: #2d287a; line-height: 1.6;"
                    ),
                    shiny::tags$span(
                      style = about_box_style,
                      shiny::HTML("<b>Maintenance:</b>")
                    ),
                    " ",
                    shiny::tags$a(
                      href = "mailto:mathieu.poirier@icm-institute.org",
                      "mathieu.poirier@icm-institute.org"
                    )
                  ),
                  shiny::div(
                    style = paste0(
                      "text-align: left; margin-top: 1rem; ",
                      "font-size: 1.5rem; color: #2d287a; line-height: 1.6;"
                    ),
                    shiny::tags$span(
                      style = about_box_style,
                      shiny::HTML("<b>Acknowledgements:</b>")
                    ),
                    " ",
                    shiny::tags$span(
                      style = "color: #4b5563;",
                      shiny::HTML(paste0(
                        "TBD"
                      ))
                    )
                  )
                )
              ),

              # Gene Table tab
              shiny::tabPanel(
                "Gene Table",
                shiny::div(
                  style = title_style,
                  shiny::HTML(paste0(
                    "<strong>Putative Causal Genes for ",
                    "Cerebral Small Vessel Disease (SVD)</strong>"
                  ))
                ),
                shiny::uiOutput("filter_message_table1"),
                shiny::br(),
                shiny::div(
                  style = paste0(
                    "display: flex; align-items: center; ",
                    "gap: 12px; margin-bottom: 1rem;"
                  ),
                  shiny::div(
                    style = tip_box_style,
                    shiny::HTML(paste0(
                      "<strong>Tip:</strong> Elements with a grey background ",
                      "like this have tooltips. ",
                      "Hover over them to see additional information."
                    ))
                  ),
                  shiny::div(
                    style = "width: 2px; height: 40px; background-color: grey;"
                  ),
                  shiny::div(
                    style = tip_box_style,
                    shiny::HTML(paste0(
                      "<strong>Tip:</strong> Elements with a ",
                      "<span style=\"color: #347bb7;\">blue font</span> ",
                      "are clickable links that open in a new tab."
                    ))
                  ),
                  shiny::div(
                    style = "width: 2px; height: 40px; background-color: grey;"
                  ),
                  shiny::div(
                    style = tip_box_style,
                    shiny::HTML(paste0(
                      "<strong>Tip:</strong> Definitions for affected ",
                      "pathways are according to ",
                      "<a href=\"https://geneontology.org/docs/",
                      "ontology-documentation/\" target=\"_blank\" ",
                      "rel=\"noopener\" style=\"color: #2d287a; ",
                      "text-decoration: underline;\">GO terminology</a>"
                    ))
                  )
                ),
                DT::dataTableOutput("firstTable")
              ),

              # Phenogram tab
              shiny::tabPanel(
                "Phenogram",
                shiny::br(),
                shiny::div(
                  style = title_style,
                  shiny::HTML("<strong>Interactive Phenogram</strong>")
                ),
                shiny::div(
                  style = "text-align: center;",
                  shiny::div(
                    style = paste0(tip_box_style, " margin-bottom: 1rem;"),
                    shiny::HTML(paste0(
                      "<strong>Tip:</strong> Hover over coloured elements ",
                      "(GWAS phenotypes) to view more information."
                    ))
                  )
                ),
                shiny::tags$iframe(
                  src = "phenogram_template.html",
                  style = paste0(
                    "width: 100%; height: 800px; ",
                    "background: white; border: none;"
                  )
                )
              ),

              # Clinical Trials Table tab
              shiny::tabPanel(
                "Clinical Trials Table",
                shiny::div(
                  style = title_style,
                  shiny::HTML(paste0(
                    "<strong>Drugs Used in Planned or Ongoing ",
                    "Clinical Trials for Cerebral Small Vessel ",
                    "Disease (SVD)</strong>"
                  ))
                ),
                shiny::uiOutput("filter_message_table2"),
                shiny::br(),
                shiny::div(
                  style = paste0(
                    "display: flex; align-items: center; ",
                    "gap: 12px; margin-bottom: 1rem;"
                  ),
                  shiny::div(
                    style = tip_box_style,
                    shiny::HTML(paste0(
                      "<strong>Tip:</strong> Elements with a grey background ",
                      "like this have tooltips. ",
                      "Hover over them to see additional information."
                    ))
                  ),
                  shiny::div(
                    style = "width: 2px; height: 40px; background-color: grey;"
                  ),
                  shiny::div(
                    style = tip_box_style,
                    shiny::HTML(paste0(
                      "<strong>Tip:</strong> Elements with a ",
                      "<span style=\"color: #347bb7;\">blue font</span> ",
                      "are clickable links that open in a new tab."
                    ))
                  ),
                  shiny::div(
                    style = "width: 2px; height: 40px; background-color: grey;"
                  ),
                  shiny::div(
                    style = tip_box_style,
                    shiny::HTML(paste0(
                      "<strong>Tip:</strong> ",
                      "Definitions for genetic targets and genetic ",
                      "evidence are according to ",
                      "<a href=\"https://platform.opentargets.org/\" ",
                      "target=\"_blank\" rel=\"noopener\" ",
                      "style=\"color: #2d287a; text-decoration: underline;\">",
                      "Open Targets</a>"
                    ))
                  )
                ),
                DT::dataTableOutput("secondTable")
              ),

              # Clinical Trials Visualization tab
              shiny::tabPanel(
                "Clinical Trials Visualization",
                shiny::br(),
                shiny::div(
                  style = title_style,
                  shiny::HTML(paste0(
                    "<strong>SVD Drugs Tested in Planned or ",
                    "Ongoing Clinical Trials</strong>"
                  ))
                ),
                shiny::div(
                  style = paste0(
                    "display: flex; align-items: center; ",
                    "justify-content: center; ",
                    "gap: 12px; margin-bottom: 1rem;"
                  ),
                  shiny::div(
                    style = tip_box_style,
                    shiny::HTML(paste0(
                      "<strong>Tip:</strong> Hover over plot elements to ",
                      "display tooltips,<br>or click on a drug name or ",
                      "drug marker to display sidepanel."
                    ))
                  ),
                  shiny::div(
                    style = "width: 2px; height: 40px; background-color: grey;"
                  ),
                  shiny::div(
                    style = tip_box_style,
                    shiny::HTML(paste0(
                      "<strong>Visually-inspired by:</strong> Fig. 1 ",
                      "in Cummings, J. <i>et al.</i>,<br>",
                      "Alzheimer's ",
                      "disease drug ",
                      "development pipeline: 2023,<br>",
                      "<i>Alzheimer's ",
                      "Dement.</i> (May 2023) ",
                      "<a href=\"https://pubmed.ncbi.nlm.nih.gov/37251912/\" ",
                      "target=\"_blank\" rel=\"noopener\" ",
                      "style=\"color: #2d287a; text-decoration: underline;\">",
                      "DOI: 10.1002/trc2.12385</a>"
                    ))
                  ),
                ),
                shiny::tags$div(
                  style = "overflow: hidden; position: relative;",
                  shiny::tags$iframe(
                    src = "python_plot.html",
                    style = paste0(
                      "width: 100%; height: 800px; ",
                      "background: white; border: none; ",
                      "display: block; overflow: hidden; ",
                      "clip-path: inset(0);"
                    )
                  )
                )
              )
            )
          )
        )
      )
    )
  )
}
# nolint end: object_usage_linter.
