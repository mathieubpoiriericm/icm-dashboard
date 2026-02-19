dt <- qs::qread("data/qs/table1_clean.qs")

dt <- dt[ , c("Gene", "GWAS Trait", "Mendelian Randomization", "Evidence From Other Omics Studies", "References")]

dt$`GWAS Trait` <- vapply(dt$`GWAS Trait`, paste, character(1L), collapse = ", ")

dt$`Evidence From Other Omics Studies` <- vapply(dt$`Evidence From Other Omics Studies`, paste, character(1L), collapse = ", ")

dt$References <- vapply(dt$References, paste, character(1L), collapse = ", ")

dt <- dt[dt$References != "(reference needed)", ]

# dt <- dt[grepl("^\\d{8}$", dt$References), ]

colnames(dt) <- c("gene", "gwas_trait", "mr", "omics", "pmid")

write.csv(dt, './data/test_data/gold_standard_base.csv', row.names = FALSE)