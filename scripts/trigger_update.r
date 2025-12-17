# trigger_update.R (PLANNED)
# Called after Python pipeline updates the database
# Regenerates all RData objects used by the Shiny dashboard

library(reticulate)

# Run Python pipeline (updates PostgreSQL database)
system2("python", c("-m", "pipeline.run_pipeline"))

# Source database connection and cleaning functions
source("R/db_connection.R")
source("R/clean_table1.R")
source("R/clean_table2.R")

# Connect to database
db_pool <- get_db_pool()

# Fetch and clean Table 1 (genes) from database
message("Fetching Table 1 from database...")
table1_raw <- fetch_table1_from_db(db_pool)
table1_clean <- clean_table1_from_df(table1_raw)
save(table1_clean, file = "data/rdata/table1_clean.RData")

# Fetch and clean Table 2 (clinical trials) from database
message("Fetching Table 2 from database...")
table2_raw <- fetch_table2_from_db(db_pool)
table2_clean <- clean_table2_from_df(table2_raw)
save(table2_clean, file = "data/rdata/table2_clean.RData")

# Fetch updated external data for any new genes
source("R/fetch_ncbi_gene_data.R")
source("R/fetch_uniprot_data.R")
source("R/fetch_omim_data.R")
source("R/fetch_pubmed_data.R")

# Close database connection pool
pool::poolClose(db_pool)

# Note: These scripts update the following RData files:
# - data/rdata/gene_info_results_df.RData (NCBI gene info)
# - data/rdata/gene_info_table2.RData (gene info for Table 2)
# - data/rdata/prot_info_clean.RData (UniProt protein info)
# - data/rdata/refs.RData (PubMed references)

message("Dashboard data updated successfully")
