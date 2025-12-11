# Zsh completion for scripts/tuning/run_experiment.sh
#
# Source in .zshrc:  source /path/to/scripts/tuning/run_experiment.completion.zsh

_run_experiment() {
  local curcontext="$curcontext" state line
  local -a flags

  flags=(
    '--help[Show usage information and exit]'
    '--fast[Low effort + 16K max_tokens for ~3x faster iteration]'
    '--repeats[Run the same config N times to measure variance]: :_guard "[0-9]#" number'
  )

  # Check if the current word is a PIPELINE_ env-var assignment
  if [[ "$words[CURRENT]" == PIPELINE_*=* ]]; then
    local varname="${words[CURRENT]%%=*}"
    local prefix="${varname}="
    case "$varname" in
      PIPELINE_LLM_MODEL)
        local -a models=(
          "${prefix}claude-sonnet-4-6"
          "${prefix}claude-opus-4-6"
          "${prefix}claude-haiku-4-5-20251001"
        )
        compadd -Q -- "${models[@]}"
        return
        ;;
      PIPELINE_LLM_EFFORT)
        local -a efforts=("${prefix}low" "${prefix}high" "${prefix}max")
        compadd -Q -- "${efforts[@]}"
        return
        ;;
      PIPELINE_PROMPT_VERSION)
        local -a versions=("${prefix}v1" "${prefix}v2" "${prefix}v3" "${prefix}v4" "${prefix}v5")
        compadd -Q -- "${versions[@]}"
        return
        ;;
      PIPELINE_LLM_MAX_TOKENS)
        local -a tokens=("${prefix}16000" "${prefix}32000" "${prefix}64000")
        compadd -Q -- "${tokens[@]}"
        return
        ;;
      PIPELINE_CONFIDENCE_THRESHOLD)
        local -a thresholds=("${prefix}0.50" "${prefix}0.60" "${prefix}0.70" "${prefix}0.80" "${prefix}0.90")
        compadd -Q -- "${thresholds[@]}"
        return
        ;;
    esac
    return
  fi

  # Complete bare PIPELINE_ prefix to env-var names with trailing =
  if [[ "$words[CURRENT]" == PIPELINE_* ]]; then
    local -a envvars=(
      'PIPELINE_LLM_MODEL='
      'PIPELINE_LLM_EFFORT='
      'PIPELINE_LLM_MAX_TOKENS='
      'PIPELINE_PROMPT_VERSION='
      'PIPELINE_CONFIDENCE_THRESHOLD='
    )
    compadd -Q -S '' -- "${envvars[@]}"
    return
  fi

  _arguments -s -S \
    "${flags[@]}" \
    '1::threshold:(0.50 0.60 0.70 0.80 0.90)' \
    '2::notes:' \
    '3::pdf_path:_run_experiment_pdf_path' \
    && return
}

_run_experiment_pdf_path() {
  _path_files -W data/test_data/pdf -g '*.pdf' -/
}

compdef _run_experiment run_experiment.sh ./scripts/tuning/run_experiment.sh scripts/tuning/run_experiment.sh
