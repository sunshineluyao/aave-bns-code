#!/usr/bin/env Rscript
# Reference implementation for the locked empirical stage.
# This file is not executed against the synthetic CI fixture.

suppressPackageStartupMessages({
  library(data.table)
  library(did)
  library(fixest)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: Rscript analysis/r/staggered_did.R causal_panel.csv output_directory")
}

panel <- fread(args[[1]])
out_dir <- args[[2]]
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

required <- c("unit_id", "period_id", "first_treat_period", "outcome")
missing <- setdiff(required, names(panel))
if (length(missing) > 0) stop(paste("Missing columns:", paste(missing, collapse = ", ")))

att <- att_gt(
  yname = "outcome",
  tname = "period_id",
  idname = "unit_id",
  gname = "first_treat_period",
  data = panel,
  panel = TRUE,
  control_group = "notyettreated",
  bstrap = TRUE,
  clustervars = "unit_id"
)

dynamic <- aggte(att, type = "dynamic")
fwrite(as.data.table(dynamic), file.path(out_dir, "group_time_att.csv"))

# Transparent TWFE benchmark only; not the preferred staggered-treatment estimator.
twfe <- feols(outcome ~ i(event_time, treated, ref = -1) | unit_id + period_id,
              cluster = ~unit_id, data = panel)
etable(twfe, file = file.path(out_dir, "twfe_event_study.tex"), replace = TRUE)
