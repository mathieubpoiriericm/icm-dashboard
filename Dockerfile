FROM rocker/shiny:4.5.3

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

WORKDIR /srv/shiny-server

# Force Posit Package Manager binaries for the rocker base (Ubuntu Noble = R 4.5.x).
# PPM serves precompiled .deb-style R packages, so restore takes minutes
# instead of hours of source compilation.
ENV RENV_CONFIG_REPOS_OVERRIDE=https://packagemanager.posit.co/cran/__linux__/noble/latest \
    RENV_CONFIG_INSTALL_VERBOSE=TRUE \
    RENV_PATHS_CACHE=/opt/renv/cache

RUN R -e "options(timeout = 120, download.file.method = 'libcurl'); \
  install.packages('renv', repos = '${RENV_CONFIG_REPOS_OVERRIDE}')"

# Copy only the files needed for renv::restore() first so Docker can cache
# the (slow) package-install layer when only app source changes.
COPY renv.lock renv.lock
COPY .Rprofile .Rprofile
COPY renv/activate.R renv/activate.R
COPY renv/settings.json renv/settings.json

RUN R -e "renv::restore(prompt = FALSE)" \
  && R -e "if (!requireNamespace('qs2', quietly = TRUE)) stop('qs2 package failed to install')"

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
