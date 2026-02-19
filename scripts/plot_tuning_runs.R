#!/usr/bin/env Rscript
# Visualize LLM tuning run results from logs/tuning/tuning_runs.csv
# Publication-ready 3-panel figure

library(ggplot2)
library(dplyr)
library(tidyr)
library(patchwork)
library(readr)
library(ggrepel)

# -- Read data ----------------------------------------------------------------
csv_path <- file.path("logs", "tuning", "tuning_runs.csv")
stopifnot(file.exists(csv_path))
runs <- readr::read_csv(csv_path, show_col_types = FALSE)

# -- Derived columns ----------------------------------------------------------
runs <- runs |>
  mutate(
    run_label = paste0("Run ", run_id),
    run_short = as.character(run_id)
  )

best_idx <- which.max(runs$composite_score)
best_run <- runs[best_idx, ]

# -- Palette & theme ----------------------------------------------------------
prompt_colors <- c(
  "v2" = "#2d287a",
  "v3" = "#4a7ceb",
  "v4" = "#d94f3b"
)

theme_pub <- function(base_size = 11) {
  theme_minimal(base_size = base_size) %+replace%
    theme(
      text = element_text(color = "grey20"),
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
  geom_point(aes(fill = prompt_version),
             shape = 21, size = 4.5, stroke = 0.5, color = "white") +
  geom_point(
    data = best_run, aes(x = reorder(run_label, run_id)),
    shape = 21, size = 8, stroke = 1.5, fill = NA, color = "#e6a817"
  ) +
  geom_text(
    aes(label = paste0("t=", confidence_threshold)),
    vjust = -1.5, size = 2.8, color = "grey50"
  ) +
  annotate(
    "label",
    x = best_run$run_label,
    y = best_run$composite_score + (y_hi - y_lo) * 0.18,
    label = paste0(
      "Best: ", best_run$prompt_version,
      " | t=", best_run$confidence_threshold,
      "\nScore: ", sprintf("%.4f", best_run$composite_score)
    ),
    size = 2.9, fontface = "bold",
    fill = "#fdf6e3", color = "grey20",
    label.r = unit(0.25, "lines"),
    label.padding = unit(0.35, "lines")
  ) +
  scale_color_manual(values = prompt_colors) +
  scale_fill_manual(values = prompt_colors) +
  scale_y_continuous(
    limits = c(y_lo, y_hi),
    breaks = scales::pretty_breaks(6),
    labels = scales::label_number(accuracy = 0.01)
  ) +
  labs(
    title = "Composite Score by Configuration",
    subtitle = "Confidence threshold shown above each point; gold ring marks the best run",
    x = NULL, y = "Composite Score", fill = "Prompt"
  ) +
  theme_pub() +
  theme(
    panel.grid.major.x = element_blank(),
    legend.position = "top",
    legend.justification = "left"
  ) +
  guides(
    color = "none",
    fill = guide_legend(override.aes = list(size = 4, stroke = 0.3))
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
    inherit.aes = FALSE
  ) +
  geom_point(
    aes(fill = prompt_version, size = f1),
    shape = 21, stroke = 0.5, color = "white", alpha = 0.92
  ) +
  geom_point(
    data = best_run, aes(size = f1),
    shape = 21, stroke = 1.5, fill = NA, color = "#e6a817"
  ) +
  geom_text_repel(
    aes(label = paste0("#", run_id), color = prompt_version),
    size = 3.2, fontface = "bold",
    point.padding = 0.5, box.padding = 0.55,
    min.segment.length = 0.3, segment.color = "grey75",
    segment.size = 0.3, max.overlaps = 20,
    show.legend = FALSE
  ) +
  scale_fill_manual(values = prompt_colors) +
  scale_color_manual(values = prompt_colors) +
  scale_size_continuous(
    range = c(3.5, 10),
    breaks = c(0.30, 0.40, 0.50),
    name = "F1 Score"
  ) +
  scale_x_continuous(
    labels = scales::label_number(accuracy = 0.01),
    limits = x_lim
  ) +
  scale_y_continuous(
    labels = scales::label_number(accuracy = 0.01),
    limits = y_lim
  ) +
  labs(
    title = "Precision\u2013Recall Tradeoff",
    subtitle = "Point size proportional to F1 score; dashed lines are F1 iso-curves",
    x = "Recall", y = "Precision", fill = "Prompt"
  ) +
  theme_pub() +
  theme(
    legend.position = "top",
    legend.justification = "left",
    legend.box = "horizontal"
  ) +
  guides(
    color = "none",
    fill = guide_legend(order = 1, override.aes = list(size = 4, stroke = 0.3)),
    size = guide_legend(order = 2)
  )


# =============================================================================
# Panel 3: Faceted metric strips
# =============================================================================

metrics_long <- runs |>
  select(run_id, run_short, prompt_version, confidence_threshold,
         precision, recall, f1, composite_score) |>
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
    aes(fill = prompt_version),
    shape = 21, size = 3, stroke = 0.4, color = "white"
  ) +
  geom_point(
    data = metric_best, aes(x = reorder(run_short, run_id)),
    shape = 21, size = 5.5, stroke = 1.2, fill = NA, color = "#e6a817"
  ) +
  scale_color_manual(values = prompt_colors) +
  scale_fill_manual(values = prompt_colors) +
  scale_y_continuous(
    labels = scales::label_number(accuracy = 0.01),
    expand = expansion(mult = c(0.02, 0.08))
  ) +
  labs(
    title = "Metric Breakdown by Run",
    subtitle = "Each panel independently scaled; best value per metric highlighted",
    x = "Run", y = NULL, fill = "Prompt"
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
    fill = guide_legend(override.aes = list(size = 4, stroke = 0.3))
  )


# =============================================================================
# Combine and save
# =============================================================================

combined <- (p1 / p2 / p3) +
  plot_layout(heights = c(1, 1.15, 0.9)) +
  plot_annotation(
    title = "LLM Tuning Runs \u2014 Configuration Comparison",
    subtitle = paste0(
      "10 runs across prompt versions (v2, v3, v4) ",
      "and confidence thresholds (0.4\u20130.75)  |  ",
      "Model: claude-opus-4-6"
    ),
    theme = theme(
      plot.title = element_text(
        size = 17, face = "bold", color = "grey10",
        margin = margin(b = 2)
      ),
      plot.subtitle = element_text(
        size = 11, color = "grey50", margin = margin(b = 12)
      ),
      plot.margin = margin(12, 12, 10, 12)
    )
  )

out_path <- file.path("logs", "tuning", "tuning_runs.png")
ggsave(out_path, combined,
       width = 12, height = 14, dpi = 300, bg = "white")
cat("Saved:", out_path, "\n")
