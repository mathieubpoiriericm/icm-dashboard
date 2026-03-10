FROM rocker/shiny:4.5.2

RUN apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=2 update \
  && apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=2 \
  install -y --no-install-recommends \
  libfreetype6-dev \
  libpng-dev \
  libfontconfig1-dev \
  libwebp-dev \
  libcurl4-openssl-dev \
  libssl-dev \
  libpq-dev \
  libzstd-dev \
  liblz4-dev \
  libxml2-dev \
  libharfbuzz-dev \
  libfribidi-dev \
  libtiff-dev \
  libjpeg-dev \
  libgdal-dev \
  libgeos-dev \
  libproj-dev \
  libsqlite3-dev \
  libudunits2-dev \
  libtbb-dev \
  zlib1g-dev \
  cmake \
  curl \
  && rm -rf /var/lib/apt/lists/*

RUN rm -rf /opt/shiny-server/samples
RUN rm -rf /srv/shiny-server/*

# Install R dependencies from renv.lock for reproducible builds
COPY renv.lock /tmp/renv.lock
RUN R -e "\
  options(timeout = 60, download.file.method = 'libcurl'); \
  install.packages('renv'); \
  renv::restore(lockfile = '/tmp/renv.lock', prompt = FALSE)" \
  && R -e "if (!requireNamespace('qs', quietly = TRUE)) stop('qs package failed to install')" \
  && rm /tmp/renv.lock

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
