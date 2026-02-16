FROM rocker/shiny:4.4.3

RUN apt-get update && apt-get install -y --no-install-recommends \
  libfreetype6-dev \
  libpng-dev \
  libfontconfig1-dev \
  libwebp-dev \
  libcurl4-openssl-dev \
  libssl-dev \
  libzstd-dev \
  liblz4-dev \
  curl \
  && rm -rf /var/lib/apt/lists/*

RUN R -e " \
  install.packages('qs', verbose = TRUE); \
  if (!requireNamespace('qs', quietly = TRUE)) stop('qs package failed to install') \
  "

RUN R -e "install.packages(c( \
  'bslib', 'dplyr', 'purrr', 'stringr', 'DT', \
  'data.table', 'sysfonts', 'showtext', 'fastmap', 'memoise', \
  'cachem', 'digest', 'jsonlite', 'shinyWidgets', \
  'httr2', 'htmltools', 'leaflet', 'tidygeocoder', \
  'future', 'future.apply' \
  ))"

RUN rm -rf /opt/shiny-server/samples
RUN rm -rf /srv/shiny-server/*

COPY app.R /srv/shiny-server
COPY R /srv/shiny-server/R
COPY www /srv/shiny-server/www

# Static CSV data files (baked into image)
COPY data/csv /srv/shiny-server/data/csv

# QS data files are mounted via PVC at runtime (from pipeline CronJob)
RUN mkdir -p /srv/shiny-server/data/qs

# Set ownership to shiny user and ensure correct permissions
RUN chown -R shiny:shiny /srv/shiny-server \
  && chmod -R 755 /srv/shiny-server/ \
  && chown -R shiny:shiny /var/log/shiny-server \
  && chown -R shiny:shiny /var/lib/shiny-server

USER shiny

EXPOSE 3838

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -sf http://localhost:3838/ || exit 1

CMD ["/usr/bin/shiny-server"]
