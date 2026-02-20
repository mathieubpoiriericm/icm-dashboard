#!/usr/bin/env Rscript
# Visualize LLM tuning run results from logs/tuning/tuning_runs.csv
# Publication-ready multi-panel figure

library(ggplot2)
library(dplyr)
library(tidyr)
library(patchwork)
library(ragg)
library(readr)
library(ggrepel)
library(ggtext)
library(systemfonts)

# -- Register Roboto from local TTF ------------------------------------------
systemfonts::register_font(
  name    = "Roboto",
  plain   = "www/fonts/Roboto-Regular.ttf",
  bold    = "www/fonts/Roboto-Bold.ttf"
)

# -- Read data ----------------------------------------------------------------
csv_path <- file.path("logs", "tuning", "tuning_runs.csv")
stopifnot(file.exists(csv_path))
runs <- readr::read_csv(csv_path, show_col_types = FALSE)

# -- Derived columns ----------------------------------------------------------
runs <- runs |>
  mutate(
    run_label = paste0("Run ", run_id),
    run_short = as.character(run_id),
    # Extract model family from full model name (e.g. "claude-opus-4-6" -> "Opus")
    model_family = dplyr::case_when(
      grepl("opus", llm_model, ignore.case = TRUE)   ~ "Opus",
      grepl("sonnet", llm_model, ignore.case = TRUE)  ~ "Sonnet",
      grepl("haiku", llm_model, ignore.case = TRUE)   ~ "Haiku",
      .default = "Other"
    ),
    # Model version: use column if present, else derive from llm_model
    model_version = dplyr::case_when(
      !is.na(model_version) & model_version != "" ~ model_version,
      grepl("claude-(opus|sonnet|haiku)-(\\d+)-(\\d+)", llm_model) ~
        sub(".*claude-(?:opus|sonnet|haiku)-(\\d+)-(\\d+).*", "\\1.\\2", llm_model, perl = TRUE),
      .default = "unknown"
    ),
    # Effort label (NA-safe for old rows)
    effort_label = dplyr::if_else(
      is.na(llm_effort) | llm_effort == "", "high", llm_effort
    ),
    # Combined config badge for labels
    config_badge = paste0(model_family, " ", model_version, "/", effort_label)
  )

# Ensure numeric columns added later are numeric (NA for old rows)
for (col in c("input_tokens", "output_tokens", "thinking_tokens",
              "total_processing_time", "llm_time")) {
  if (col %in% names(runs)) {
    runs[[col]] <- as.numeric(runs[[col]])
  }
}

best_idx <- which.max(runs$composite_score)
best_run <- runs[best_idx, ]

# Model shapes: consistent across all panels
model_shapes <- c("Opus" = 21, "Sonnet" = 24, "Haiku" = 22, "Other" = 23)
has_multiple_models <- length(unique(runs$model_family)) > 1L

# -- Palette & theme ----------------------------------------------------------
prompt_colors <- c(
  "v2" = "#2d287a",
  "v3" = "#4a7ceb",
  "v4" = "#d94f3b",
  "v5" = "#35b779"
)

# Weight color palettes for infographic panel
weight_colors <- c(
  "F1"       = "#2d287a",
  "GWAS_J"   = "#4a7ceb",
  "MR_acc"   = "#35b779",
  "Omics_J"  = "#e6a817",
  "PMID_R"   = "#d94f3b",
  "Gene_sc"  = "#6c757d"
)

gene_weight_colors <- c(
  "Detection" = "#2d287a",
  "GWAS_J"    = "#4a7ceb",
  "MR"        = "#35b779",
  "Omics_J"   = "#e6a817",
  "PMID_R"    = "#d94f3b"
)

theme_pub <- function(base_size = 11) {
  theme_minimal(base_size = base_size) %+replace%
    theme(
      text = element_text(family = "Roboto", color = "grey20"),
      plot.title = element_text(
        size = base_size + 2, face = "bold", color = "grey10",
        margin = margin(b = 3), hjust = 0
      ),
      plot.subtitle = element_text(
        size = base_size - 1, color = "grey50",
        margin = margin(b = 10), hjust = 0
      ),
      axis.title = element_text(size = base_size, color = "grey30"),
      axis.text = element_text(size = base_size - 1, color = "grey40"),
      panel.grid.major = element_line(color = "grey92", linewidth = 0.4),
      panel.grid.minor = element_blank(),
      legend.title = element_text(size = base_size - 1, face = "bold"),
      legend.text = element_text(size = base_size - 1),
      legend.key.size = unit(0.9, "lines"),
      plot.margin = margin(12, 14, 8, 14),
      strip.text = element_text(
        size = base_size, face = "bold", color = "grey20"
      )
    )
}

# =============================================================================
# Panel 1: Composite Score lollipop
# =============================================================================

score_range <- range(runs$composite_score)
y_pad <- (score_range[2] - score_range[1]) * 0.18
y_lo <- score_range[1] - y_pad
y_hi <- score_range[2] + y_pad * 2.5

p1 <- ggplot(runs, aes(x = reorder(run_label, run_id), y = composite_score)) +
  geom_segment(
    aes(xend = reorder(run_label, run_id), y = y_lo, yend = composite_score,
        color = prompt_version),
    linewidth = 0.9, lineend = "round"
  ) +
  geom_point(aes(fill = prompt_version, shape = model_family),
             size = 4.5, stroke = 0.5, color = "white") +
  geom_point(
    data = best_run, aes(x = reorder(run_label, run_id)),
    shape = 21, size = 8, stroke = 1.5, fill = NA, color = "#e6a817"
  ) +
  geom_text(
    aes(label = paste0("t=", confidence_threshold)),
    vjust = -1.5, size = 2.8, color = "grey50", family = "Roboto"
  ) +
  annotate(
    "label",
    x = best_run$run_label,
    y = best_run$composite_score + (y_hi - y_lo) * 0.18,
    label = paste0(
      "Best: ", best_run$prompt_version,
      " | t=", best_run$confidence_threshold,
      " | ", best_run$config_badge,
      "\nScore: ", sprintf("%.4f", best_run$composite_score)
    ),
    hjust = dplyr::if_else(best_idx > nrow(runs) * 0.6, 1, 0.5),
    size = 2.8, fontface = "bold", family = "Roboto",
    fill = "#fdf6e3", color = "grey20",
    label.r = unit(0.25, "lines"),
    label.padding = unit(0.35, "lines")
  ) +
  scale_color_manual(values = prompt_colors) +
  scale_fill_manual(values = prompt_colors) +
  scale_shape_manual(values = model_shapes) +
  scale_y_continuous(
    limits = c(y_lo, y_hi),
    breaks = scales::pretty_breaks(6),
    labels = scales::label_number(accuracy = 0.01)
  ) +
  labs(
    title = "Composite Score by Configuration",
    subtitle = "Confidence threshold shown above each point; gold ring marks the best run",
    x = NULL, y = "Composite Score", fill = "Prompt", shape = "Model"
  ) +
  theme_pub() +
  theme(
    panel.grid.major.x = element_blank(),
    legend.position = "top",
    legend.justification = "left"
  ) +
  guides(
    color = "none",
    fill = guide_legend(
      order = 1, override.aes = list(size = 4, stroke = 0.3, shape = 21)
    ),
    shape = if (has_multiple_models) {
      guide_legend(order = 2, override.aes = list(size = 4, fill = "grey60"))
    } else {
      "none"
    }
  )


# =============================================================================
# Panel 2: Precision vs Recall with F1 iso-curves
# =============================================================================

# Axis bounds
x_lim <- c(0.28, 0.96)
y_lim <- c(0.15, 0.48)

# F1 iso-curves clipped to axis bounds
f1_levels <- c(0.25, 0.35, 0.45, 0.55)
iso_data <- do.call(rbind, lapply(f1_levels, function(f) {
  r_seq <- seq(max(f / 2 + 0.001, x_lim[1]), x_lim[2], length.out = 300)
  p_seq <- (f * r_seq) / (2 * r_seq - f)
  keep <- p_seq >= y_lim[1] & p_seq <= y_lim[2] & p_seq > 0
  data.frame(recall = r_seq[keep], precision = p_seq[keep], f1_val = f)
}))

# Label positions — rightmost point per curve
iso_labels <- iso_data |>
  group_by(f1_val) |>
  slice_max(recall, n = 1L) |>
  mutate(recall = recall - 0.03) |>
  ungroup()

p2 <- ggplot(runs, aes(x = recall, y = precision)) +
  geom_line(
    data = iso_data,
    aes(x = recall, y = precision, group = f1_val),
    color = "grey85", linewidth = 0.4, linetype = "dashed",
    inherit.aes = FALSE
  ) +
  geom_text(
    data = iso_labels,
    aes(x = recall, y = precision, label = paste0("F1=", f1_val)),
    size = 2.4, color = "grey72", hjust = -0.1, vjust = -0.4,
    family = "Roboto", inherit.aes = FALSE
  ) +
  geom_point(
    aes(fill = prompt_version, shape = model_family, size = f1),
    stroke = 0.5, color = "white", alpha = 0.92
  ) +
  geom_point(
    data = best_run, aes(size = f1),
    shape = 21, stroke = 1.5, fill = NA, color = "#e6a817"
  ) +
  geom_text_repel(
    aes(label = paste0("#", run_id), color = prompt_version),
    size = 3.0, fontface = "bold", family = "Roboto",
    point.padding = 0.5, box.padding = 0.55,
    min.segment.length = 0.3, segment.color = "grey75",
    segment.size = 0.3, max.overlaps = 20,
    show.legend = FALSE
  ) +
  scale_fill_manual(values = prompt_colors) +
  scale_color_manual(values = prompt_colors) +
  scale_shape_manual(values = model_shapes) +
  scale_size_continuous(
    range = c(3.5, 10),
    breaks = c(0.30, 0.40, 0.50),
    limits = c(
      min(runs$f1) - 0.02,
      max(runs$f1) + 0.02
    ),
    name = "F1 Score"
  ) +
  scale_x_continuous(
    labels = scales::label_number(accuracy = 0.01)
  ) +
  scale_y_continuous(
    labels = scales::label_number(accuracy = 0.01)
  ) +
  coord_cartesian(xlim = x_lim, ylim = y_lim) +
  labs(
    title = "Precision\u2013Recall Tradeoff",
    subtitle = "Point size proportional to F1 score; dashed lines are F1 iso-curves",
    x = "Recall", y = "Precision", fill = "Prompt", shape = "Model"
  ) +
  theme_pub() +
  theme(
    legend.position = "top",
    legend.justification = "left",
    legend.box = "horizontal"
  ) +
  guides(
    color = "none",
    fill = guide_legend(
      order = 1, override.aes = list(size = 4, stroke = 0.3, shape = 21)
    ),
    shape = if (has_multiple_models) {
      guide_legend(order = 2, override.aes = list(size = 4, fill = "grey60"))
    } else {
      "none"
    },
    size = guide_legend(order = 3)
  )


# =============================================================================
# Panel 3: Faceted metric strips
# =============================================================================

metrics_long <- runs |>
  select(run_id, run_short, prompt_version, model_family,
         confidence_threshold, precision, recall, f1, composite_score) |>
  pivot_longer(
    cols = c(precision, recall, f1, composite_score),
    names_to = "metric",
    values_to = "value"
  ) |>
  mutate(metric = factor(metric,
    levels = c("precision", "recall", "f1", "composite_score"),
    labels = c("Precision", "Recall", "F1", "Composite Score")
  ))

metric_best <- metrics_long |>
  group_by(metric) |>
  slice_max(value, n = 1L, with_ties = FALSE) |>
  ungroup()

p3 <- ggplot(metrics_long, aes(x = reorder(run_short, run_id), y = value)) +
  facet_wrap(~metric, nrow = 1, scales = "free_y") +
  geom_segment(
    aes(xend = reorder(run_short, run_id), y = 0, yend = value,
        color = prompt_version),
    linewidth = 0.6, alpha = 0.35
  ) +
  geom_point(
    aes(fill = prompt_version, shape = model_family),
    size = 3, stroke = 0.4, color = "white"
  ) +
  geom_point(
    data = metric_best, aes(x = reorder(run_short, run_id)),
    shape = 21, size = 5.5, stroke = 1.2, fill = NA, color = "#e6a817"
  ) +
  scale_color_manual(values = prompt_colors) +
  scale_fill_manual(values = prompt_colors) +
  scale_shape_manual(values = model_shapes) +
  scale_y_continuous(
    labels = scales::label_number(accuracy = 0.01),
    expand = expansion(mult = c(0.02, 0.08))
  ) +
  labs(
    title = "Metric Breakdown by Run",
    subtitle = "Each panel independently scaled; best value per metric highlighted",
    x = "Run", y = NULL, fill = "Prompt", shape = "Model"
  ) +
  theme_pub() +
  theme(
    panel.grid.major.x = element_blank(),
    legend.position = "top",
    legend.justification = "left",
    axis.text.x = element_text(size = 9),
    strip.background = element_rect(fill = "grey96", color = NA),
    panel.spacing = unit(1.2, "lines")
  ) +
  guides(
    color = "none",
    fill = guide_legend(
      order = 1, override.aes = list(size = 4, stroke = 0.3, shape = 21)
    ),
    shape = if (has_multiple_models) {
      guide_legend(order = 2, override.aes = list(size = 3, fill = "grey60"))
    } else {
      "none"
    }
  )


# =============================================================================
# Panel 4: Scoring Methodology Infographic
# =============================================================================

# -- Helper: build a segmented weight bar as data.frame ----------------------
make_bar <- function(weights, colors, y_center, bar_h, x_left, x_right) {
  bar_w <- x_right - x_left
  n <- length(weights)
  cum <- cumsum(weights)
  xmin <- x_left + c(0, cum[-n]) / sum(weights) * bar_w
  xmax <- x_left + cum / sum(weights) * bar_w
  data.frame(
    xmin = xmin, xmax = xmax,
    ymin = y_center - bar_h / 2, ymax = y_center + bar_h / 2,
    fill = colors[seq_len(n)],
    label = paste0(weights * 100, "%"),
    stringsAsFactors = FALSE
  )
}

comp_bar <- make_bar(
  weights = c(0.40, 0.15, 0.10, 0.15, 0.10, 0.10),
  colors  = unname(weight_colors),
  y_center = 70, bar_h = 4, x_left = 4, x_right = 48
)

gene_bar <- make_bar(
  weights = c(0.55, 0.15, 0.10, 0.10, 0.10),
  colors  = unname(gene_weight_colors),
  y_center = 70, bar_h = 4, x_left = 54, x_right = 97
)

# -- Rich-text labels --------------------------------------------------------
# Color helper: wrap text in colored span
clr <- function(txt, hex) paste0("<span style='color:", hex, "'>", txt, "</span>")

# Formula bodies only (headers rendered separately in Roboto)
composite_formula <- paste0(
  "= ", clr("0.40", weight_colors["F1"]), " \u00b7 <i>F</i><sub>1</sub>",
  " + ", clr("0.15", weight_colors["GWAS_J"]), " \u00b7 <i>GWAS</i><sub><i>J</i></sub>",
  " + ", clr("0.10", weight_colors["MR_acc"]), " \u00b7 <i>MR</i><sub><i>acc</i></sub>",
  " + ", clr("0.15", weight_colors["Omics_J"]), " \u00b7 <i>Omics</i><sub><i>J</i></sub>",
  "<br>\u2003+ ", clr("0.10", weight_colors["PMID_R"]), " \u00b7 <i>PMID</i><sub><i>R</i></sub>",
  " + ", clr("0.10", weight_colors["Gene_sc"]), " \u00b7 <i>Gene</i><sub><i>sc</i></sub>"
)

gene_formula <- paste0(
  "= ", clr("0.55", gene_weight_colors["Detection"]), " \u00b7 <i>Det</i>",
  " + ", clr("0.15", gene_weight_colors["GWAS_J"]), " \u00b7 <i>GWAS</i><sub><i>J</i></sub>",
  " + ", clr("0.10", gene_weight_colors["MR"]), " \u00b7 <i>MR</i>",
  "<br>\u2003+ ", clr("0.10", gene_weight_colors["Omics_J"]), " \u00b7 <i>Omics</i><sub><i>J</i></sub>",
  " + ", clr("0.10", gene_weight_colors["PMID_R"]), " \u00b7 <i>PMID</i><sub><i>R</i></sub>"
)

standard_formulas <- paste0(
  "<i>Precision</i> = <i>TP</i> / (<i>TP</i> + <i>FP</i>)<br>",
  "<i>Recall</i> = <i>TP</i> / (<i>TP</i> + <i>FN</i>)<br>",
  "<i>F</i><sub>1</sub> = 2 \u00b7 <i>P</i> \u00b7 <i>R</i> / (<i>P</i> + <i>R</i>)"
)

field_formulas <- paste0(
  "<i>J</i>(<i>A</i>,<i>B</i>) = |<i>A</i>\u2229<i>B</i>| / |<i>A</i>\u222a<i>B</i>|<br>",
  "<i>Set Recall</i> = |<i>Ref</i>\u2229<i>Pipe</i>| / |<i>Ref</i>|<br>",
  "<i>MR Accuracy</i> = exact boolean match"
)

fn_formulas <- paste0(
  "<i>FN</i><sub><i>threshold</i></sub> \u2014 extracted by LLM<br>",
  "\u2003but rejected at confidence threshold<br>",
  "\u2003(recoverable by lowering threshold)<br>",
  "<i>FN</i><sub><i>miss</i></sub> \u2014 never extracted by LLM<br>",
  "\u2003(true miss, not recoverable)"
)

robustness <- paste0(
  "\u2022 PMID weight redistributed when skipped\n",
  "\u2022 Gene aliases resolved (e.g. COL4A1 = COL4A1/2)\n",
  "\u2022 Detection credit = 1.0 for any matched gene\n",
  "\u2022 Composite mixes gene-level and field-level\n",
  "\u2022 Jaccard penalizes both FP and FN in sets\n",
  "\u2022 Set Recall only penalizes missing items\n",
  "\u2022 Scores bounded [0, 1]; higher is better"
)

# Section header positions (x, y, label)
headers <- data.frame(
  x = c(4, 54, 4, 54, 4, 54),
  y = c(92, 92, 58, 58, 33, 33),
  label = c("Composite Score", "Per-Gene Score",
            "Standard Metrics", "Field-Level Metrics",
            "FN Categories", "Robustness Properties"),
  stringsAsFactors = FALSE
)

# -- Assemble canvas ---------------------------------------------------------
richtext_size <- 2.8
header_size   <- 10 / .pt

p4 <- ggplot() +
  coord_cartesian(xlim = c(0, 100), ylim = c(0, 100), expand = FALSE) +

  # -- Section headers (all Roboto) --
  geom_text(
    data = headers,
    aes(x = x, y = y, label = label),
    hjust = 0, vjust = 1, size = header_size,
    fontface = "bold", family = "Roboto", color = "grey20"
  ) +

  # -- Top zone: Composite formula (serif) --
  geom_richtext(
    aes(x = 4, y = 87, label = composite_formula),
    hjust = 0, vjust = 1, size = richtext_size, family = "serif",
    fill = NA, label.color = NA, color = "grey20",
    label.padding = unit(0, "lines")
  ) +
  # Composite weight bar
  geom_rect(
    data = comp_bar,
    aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax),
    fill = comp_bar$fill, color = "white", linewidth = 0.4
  ) +
  geom_text(
    data = comp_bar,
    aes(x = (xmin + xmax) / 2, y = (ymin + ymax) / 2, label = label),
    size = 2.0, color = "white", fontface = "bold", family = "Roboto"
  ) +
  geom_text(
    data = comp_bar,
    aes(x = (xmin + xmax) / 2, y = ymin - 1.8,
        label = c("F1", "GWAS", "MR", "Omics", "PMID", "Gene")),
    size = 2.0, color = "grey50", family = "Roboto"
  ) +

  # -- Top zone: Per-Gene formula (serif) --
  geom_richtext(
    aes(x = 54, y = 87, label = gene_formula),
    hjust = 0, vjust = 1, size = richtext_size, family = "serif",
    fill = NA, label.color = NA, color = "grey20",
    label.padding = unit(0, "lines")
  ) +
  # Gene weight bar
  geom_rect(
    data = gene_bar,
    aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax),
    fill = gene_bar$fill, color = "white", linewidth = 0.4
  ) +
  geom_text(
    data = gene_bar,
    aes(x = (xmin + xmax) / 2, y = (ymin + ymax) / 2, label = label),
    size = 2.0, color = "white", fontface = "bold", family = "Roboto"
  ) +
  geom_text(
    data = gene_bar,
    aes(x = (xmin + xmax) / 2, y = ymin - 1.8,
        label = c("Detect", "GWAS", "MR", "Omics", "PMID")),
    size = 2.0, color = "grey50", family = "Roboto"
  ) +

  # -- Middle zone: Standard Metrics formulas (serif) --
  geom_richtext(
    aes(x = 4, y = 53, label = standard_formulas),
    hjust = 0, vjust = 1, size = richtext_size, family = "serif",
    fill = NA, label.color = NA, color = "grey20",
    label.padding = unit(0, "lines")
  ) +

  # -- Middle zone: Field-Level Metrics formulas (serif) --
  geom_richtext(
    aes(x = 54, y = 53, label = field_formulas),
    hjust = 0, vjust = 1, size = richtext_size, family = "serif",
    fill = NA, label.color = NA, color = "grey20",
    label.padding = unit(0, "lines")
  ) +

  # -- Bottom zone: FN Categories formulas (serif) --
  geom_richtext(
    aes(x = 4, y = 28, label = fn_formulas),
    hjust = 0, vjust = 1, size = richtext_size, family = "serif",
    fill = NA, label.color = NA, color = "grey20",
    label.padding = unit(0, "lines")
  ) +

  # -- Bottom zone: Robustness Properties (Roboto, plain text) --
  annotate(
    "text", x = 54, y = 28, label = robustness,
    hjust = 0, vjust = 1, size = richtext_size,
    family = "Roboto", color = "grey20", lineheight = 1.2
  ) +

  labs(
    title = "Scoring Methodology",
    subtitle = "Formulas and weights from validate_pipeline.py"
  ) +
  theme_pub() +
  theme(
    axis.text = element_blank(),
    axis.title = element_blank(),
    axis.ticks = element_blank(),
    axis.ticks.length = unit(0, "pt"),
    panel.grid.major = element_blank()
  )


# =============================================================================
# Panel 5: Cost & Speed Tradeoff
# =============================================================================

# Always available: estimated_cost_usd. Optional: llm_time, output_tokens.
has_cost <- "estimated_cost_usd" %in% names(runs) &&
  any(!is.na(runs$estimated_cost_usd))

if (has_cost) {
  runs <- runs |>
    mutate(
      cost = as.numeric(ifelse(estimated_cost_usd == "None", NA_character_, estimated_cost_usd)),
      # LLM time in minutes (NA for old rows without timing)
      llm_min = if ("llm_time" %in% names(runs)) {
        as.numeric(llm_time) / 60
      } else {
        NA_real_
      }
    )
  best_run <- runs[best_idx, ]  # refresh after mutate

  has_timing <- "llm_min" %in% names(runs) &&
    any(!is.na(runs$llm_min))

  # Filter to rows with valid cost for Panel 5
  runs_costed <- runs |> filter(!is.na(cost))
  best_costed <- if (!is.na(best_run$cost)) {
    best_run
  } else {
    runs_costed[which.max(runs_costed$composite_score), ]
  }

  # Left panel: Cost vs Composite Score
  p5a <- ggplot(
    runs_costed, aes(x = cost, y = composite_score)
  ) +
    geom_point(
      aes(fill = prompt_version, shape = model_family),
      size = 4.5, stroke = 0.5, color = "white"
    ) +
    geom_point(
      data = best_costed,
      shape = 21, size = 8, stroke = 1.5, fill = NA, color = "#e6a817"
    ) +
    geom_text_repel(
      aes(label = paste0("#", run_id), color = prompt_version),
      size = 3.0, fontface = "bold", family = "Roboto",
      point.padding = 0.4, box.padding = 0.45,
      min.segment.length = 0.3, segment.color = "grey75",
      segment.size = 0.3, max.overlaps = 20,
      show.legend = FALSE
    ) +
    scale_fill_manual(values = prompt_colors) +
    scale_color_manual(values = prompt_colors) +
    scale_shape_manual(values = model_shapes) +
    scale_x_continuous(
      labels = scales::label_dollar(accuracy = 0.01)
    ) +
    scale_y_continuous(
      labels = scales::label_number(accuracy = 0.01)
    ) +
    labs(
      title = "Cost vs. Quality",
      subtitle = "Lower-left = cheaper; higher = better quality",
      x = "Estimated Cost (USD)", y = "Composite Score",
      fill = "Prompt", shape = "Model"
    ) +
    theme_pub() +
    theme(
      legend.position = "top",
      legend.justification = "left",
      legend.box = "horizontal"
    ) +
    guides(
      color = "none",
      fill = guide_legend(
        order = 1,
        override.aes = list(size = 4, stroke = 0.3, shape = 21)
      ),
      shape = if (has_multiple_models) {
        guide_legend(
          order = 2, override.aes = list(size = 4, fill = "grey60")
        )
      } else {
        "none"
      }
    )

  # Right panel: LLM Time vs Composite (only if timing data exists)
  if (has_timing) {
    runs_timed <- runs |> filter(!is.na(llm_min))
    best_timed <- if (!is.na(best_run$llm_min)) {
      best_run
    } else {
      runs_timed[which.max(runs_timed$composite_score), ]
    }

    p5b <- ggplot(
      runs_timed, aes(x = llm_min, y = composite_score)
    ) +
      geom_point(
        aes(fill = prompt_version, shape = model_family),
        size = 4.5, stroke = 0.5, color = "white"
      ) +
      geom_point(
        data = best_timed,
        shape = 21, size = 8, stroke = 1.5, fill = NA, color = "#e6a817"
      ) +
      geom_text_repel(
        aes(label = paste0("#", run_id), color = prompt_version),
        size = 3.0, fontface = "bold", family = "Roboto",
        point.padding = 0.4, box.padding = 0.45,
        min.segment.length = 0.3, segment.color = "grey75",
        segment.size = 0.3, max.overlaps = 20,
        show.legend = FALSE
      ) +
      scale_fill_manual(values = prompt_colors) +
      scale_color_manual(values = prompt_colors) +
      scale_shape_manual(values = model_shapes) +
      scale_y_continuous(
        labels = scales::label_number(accuracy = 0.01)
      ) +
      labs(
        title = "LLM Time vs. Quality",
        subtitle = "Lower-left = faster; higher = better quality",
        x = "LLM Streaming Time (min)", y = "Composite Score",
        fill = "Prompt", shape = "Model"
      ) +
      theme_pub() +
      theme(
        legend.position = "top",
        legend.justification = "left",
        legend.box = "horizontal"
      ) +
      guides(
        color = "none",
        fill = guide_legend(
          order = 1,
          override.aes = list(size = 4, stroke = 0.3, shape = 21)
        ),
        shape = if (has_multiple_models) {
          guide_legend(
            order = 2, override.aes = list(size = 4, fill = "grey60")
          )
        } else {
          "none"
        }
      )

    p5 <- p5a + p5b + plot_layout(ncol = 2, guides = "collect") &
      theme(legend.position = "top", legend.justification = "left")
  } else {
    p5 <- p5a
  }
}


# =============================================================================
# Combine and save
# =============================================================================

# Thin horizontal rule separator between panels
sep <- ggplot() +
  geom_segment(aes(x = 0, xend = 1, y = 0.5, yend = 0.5),
               color = "grey60", linewidth = 0.5) +
  coord_cartesian(xlim = c(0, 1), ylim = c(0, 1), expand = FALSE) +
  theme_void() +
  theme(plot.margin = margin(0, 14, 0, 14))

# Dynamic subtitle: models, efforts, thresholds
threshold_range <- range(runs$confidence_threshold)
model_str <- paste(sort(unique(runs$model_family)), collapse = ", ")
effort_str <- paste(sort(unique(runs$effort_label)), collapse = ", ")
version_str <- paste(sort(unique(runs$model_version)), collapse = ", ")
anno_subtitle <- paste0(
  nrow(runs), " runs across prompt versions (",
  paste(sort(unique(runs$prompt_version)), collapse = ", "), "), ",
  "thresholds (",
  threshold_range[1], "\u2013", threshold_range[2], ")  |  ",
  "Models: ", model_str, " (v", version_str, ")  |  Effort: ", effort_str
)

# Assemble layout: include Panel 5 if cost data is available
if (has_cost) {
  combined <- (p1 / sep / p2 / sep / p3 / sep / p5 / sep / p4) +
    plot_layout(
      heights = c(1, 0.015, 1.15, 0.015, 0.9, 0.015, 1.15, 0.015, 1.4)
    )
  fig_height <- 26
} else {
  combined <- (p1 / sep / p2 / sep / p3 / sep / p4) +
    plot_layout(heights = c(1, 0.015, 1.15, 0.015, 0.9, 0.015, 1.4))
  fig_height <- 21
}

combined <- combined +
  plot_annotation(
    title = "LLM Tuning Runs \u2014 Configuration Comparison",
    subtitle = anno_subtitle,
    theme = theme(
      plot.title = element_text(
        family = "Roboto", size = 17, face = "bold", color = "grey10",
        margin = margin(b = 2)
      ),
      plot.subtitle = element_text(
        family = "Roboto", size = 11, color = "grey50", margin = margin(b = 12)
      ),
      plot.margin = margin(12, 12, 10, 12)
    )
  )

out_dir <- file.path("logs", "png")
if (!dir.exists(out_dir)) dir.create(out_dir, recursive = TRUE)
out_path <- file.path(out_dir, "tuning_runs.png")
ggsave(out_path, combined,
       width = 12, height = fig_height, dpi = 300, bg = "white",
       device = ragg::agg_png)
cat("Saved:", out_path, "\n")
