#!/usr/bin/env python3
"""
Architecture Diagram Generator for ICM Cerebral SVD Dashboard

This script generates a comprehensive architecture diagram for the R Shiny
application using the Python `diagrams` package.

Application: ICM Cerebral SVD Dashboard
- Multi-file modular R Shiny application
- 5 UI tabs (About, Gene Table, Phenogram, Clinical Trials Table, CT Visualization)
- 9 filter module instances
- External API integrations (NCBI, UniProt, PubMed, OMIM, Clinical Trial Registries)
- Python ETL pipeline for data updates

Requirements:
    pip install diagrams

Usage:
    python scripts/architecture_diagram.py

Output:
    scripts/svd_dashboard_architecture.png

Author: Generated with Claude Code
"""

import os

from diagrams import Cluster, Diagram, Edge
from diagrams.generic.compute import Rack
from diagrams.generic.database import SQL
from diagrams.generic.storage import Storage
from diagrams.onprem.client import Users
from diagrams.programming.language import Python, R

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def create_architecture_diagram():
    """
    Creates a comprehensive architecture diagram for the SVD Dashboard.

    The diagram shows:
    1. User interaction layer
    2. Shiny application structure (UI + Server)
    3. Data storage layer (QS, CSV, XLSX files)
    4. External API connections
    5. ETL Pipeline components
    6. Static assets and embedded visualizations
    """

    # Diagram configuration
    graph_attr = {
        "fontsize": "20",
        "bgcolor": "white",
        "pad": "0.5",
        "splines": "ortho",
        "nodesep": "0.8",
        "ranksep": "1.2",
    }

    node_attr = {
        "fontsize": "11",
        "fontname": "Roboto, Helvetica, Arial, sans-serif",
    }

    edge_attr = {
        "fontsize": "10",
        "fontname": "Roboto, Helvetica, Arial, sans-serif",
    }

    output_path = os.path.join(SCRIPT_DIR, "svd_dashboard_architecture")

    with Diagram(
        "ICM Cerebral SVD Dashboard Architecture",
        filename=output_path,
        show=False,
        direction="TB",  # Top to Bottom
        graph_attr=graph_attr,
        node_attr=node_attr,
        edge_attr=edge_attr,
        outformat="png",
    ):

        # =================================================================
        # USER LAYER
        # =================================================================
        with Cluster("Users"):
            users = Users("Web Browser\nClients")

        # =================================================================
        # SHINY APPLICATION LAYER
        # =================================================================
        with Cluster("R Shiny Application (app.R Entry Point)"):

            # UI Layer
            with Cluster("UI Layer (ui.R)"):
                with Cluster("Navigation Tabs"):
                    tab_about = Rack("About Tab\n(Value Boxes)")
                    tab_gene = Rack("Gene Table Tab\n(3 Filters + DataTable)")
                    tab_pheno = Rack("Phenogram Tab\n(iframe)")
                    tab_ct = Rack("Clinical Trials\nTable Tab\n(6 Filters + DataTable)")
                    tab_viz = Rack("CT Visualization\nTab (iframe)")

                ui_themes = Storage("bslib Themes\n(Light/Dark)")

            # Server Layer
            with Cluster("Server Layer (server.R Orchestrator)"):

                with Cluster("Filter Modules (mod_checkbox_filter.R)"):
                    # Table 1 Filters
                    filter_mr = R("mr_filter\n(MR Yes/No)")
                    filter_gwas = R("gwas_trait_filter\n(GWAS Traits)")
                    filter_omics = R("omics_filter\n(Omics Evidence)")

                    # Table 2 Filters
                    filter_ge = R("ge_filter\n(Genetic Evidence)")
                    filter_reg = R("reg_filter\n(Registry)")
                    filter_ct = R("ct_filter\n(CT Phase)")
                    filter_pop = R("pop_filter\n(SVD Population)")
                    filter_spon = R("spon_filter\n(Sponsor Type)")
                    filter_size = R("sample_size_filter\n(Sample Size)")

                with Cluster("Table Server Logic"):
                    server_table1 = R("server_table1.R\n(Gene Table)")
                    server_table2 = R("server_table2.R\n(CT Table)")

                with Cluster("Support Modules"):
                    tooltips = R("tooltips.R\n(HTML Generation)")
                    filter_utils = R("filter_utils.R\n(Filter Logic)")

            # Data Preparation
            with Cluster("Data Preparation (data_prep.R)"):
                data_prep = R("load_and_prepare_data()\nload_table2_data()")
                fastmap = Storage("fastmap Indexes\n(O(1) Filtering)")

        # =================================================================
        # DATA STORAGE LAYER
        # =================================================================
        with Cluster("Data Layer (data/)"):

            with Cluster("QS Serialized Files (data/qs/)"):
                qs_table1 = Storage("table1_clean.qs")
                qs_table2 = Storage("table2_clean.qs")
                qs_gene = Storage("gene_info*.qs")
                qs_prot = Storage("prot_info_clean.qs")
                qs_refs = Storage("refs.qs")
                qs_gwas = Storage("gwas_trait_names.qs")

            with Cluster("CSV Files (data/csv/)"):
                csv_omim = Storage("omim_info.csv")
                csv_tables = Storage("table1.csv\ntable2.csv")
                _ = Storage("phenotype data\n(phenogram)")  # Visual element only

            with Cluster("Excel Source (data/xlsx/)"):
                xlsx_source = Storage("gwas_trait_names.xlsx\ntable1_pheno.xlsx")

        # =================================================================
        # STATIC ASSETS LAYER
        # =================================================================
        with Cluster("Static Assets (www/)"):

            with Cluster("Custom Styles/Scripts"):
                css_js = Storage("custom.css/js\n(minified)")

            with Cluster("Embedded Visualizations"):
                phenogram_html = Storage("phenogram_template.html\n(Interactive)")
                python_plot = Storage("python_plot.html\n(Plotly)")

            with Cluster("Third-Party Libraries"):
                tippy = Storage("Tippy.js\nPopper.js")

        # =================================================================
        # EXTERNAL APIs LAYER
        # =================================================================
        with Cluster("External APIs"):

            with Cluster("Biomedical Databases"):
                ncbi = SQL("NCBI Gene API")
                uniprot = SQL("UniProt API")
                pubmed = SQL("PubMed API")
                omim = SQL("OMIM API")

            with Cluster("Clinical Trial Registries"):
                nct = SQL("ClinicalTrials.gov\n(NCT)")
                isrctn = SQL("ISRCTN")
                anzctr = SQL("ANZCTR")
                chictr = SQL("ChiCTR")

        # =================================================================
        # ETL PIPELINE LAYER
        # =================================================================
        with Cluster("Python ETL Pipeline (pipeline/)"):

            with Cluster("Data Acquisition"):
                pubmed_search = Python("pubmed_search.py")
                pdf_retrieval = Python("pdf_retrieval.py")

            with Cluster("Processing"):
                llm_extraction = Python("llm_extraction.py")
                validation = Python("validation.py")
                quality_metrics = Python("quality_metrics.py")

            with Cluster("Storage"):
                database = Python("database.py")
                data_merger = Python("data_merger.py")

            pipeline_main = Python("main.py\n(Orchestrator)")

        # =================================================================
        # HELPER PACKAGE
        # =================================================================
        with Cluster("Custom R Package"):
            marco = R("maRco/\n(Helper Functions)")

        # =================================================================
        # DATA FLOW CONNECTIONS
        # =================================================================

        # User to UI
        users >> Edge(label="HTTP/WebSocket", color="blue") >> tab_about
        users >> Edge(color="blue") >> tab_gene
        users >> Edge(color="blue") >> tab_pheno
        users >> Edge(color="blue") >> tab_ct
        users >> Edge(color="blue") >> tab_viz

        # UI Tabs to Server
        tab_gene >> Edge(label="Reactive", color="green") >> filter_mr
        tab_gene >> Edge(color="green") >> filter_gwas
        tab_gene >> Edge(color="green") >> filter_omics
        tab_gene >> Edge(color="green") >> server_table1

        tab_ct >> Edge(color="green") >> filter_ge
        tab_ct >> Edge(color="green") >> filter_reg
        tab_ct >> Edge(color="green") >> filter_ct
        tab_ct >> Edge(color="green") >> filter_pop
        tab_ct >> Edge(color="green") >> filter_spon
        tab_ct >> Edge(color="green") >> filter_size
        tab_ct >> Edge(color="green") >> server_table2

        # Filter modules to table servers
        filter_mr >> Edge(color="orange") >> server_table1
        filter_gwas >> Edge(color="orange") >> server_table1
        filter_omics >> Edge(color="orange") >> server_table1

        filter_ge >> Edge(color="orange") >> server_table2
        filter_reg >> Edge(color="orange") >> server_table2
        filter_ct >> Edge(color="orange") >> server_table2
        filter_pop >> Edge(color="orange") >> server_table2
        filter_spon >> Edge(color="orange") >> server_table2
        filter_size >> Edge(color="orange") >> server_table2

        # Table servers to support modules
        server_table1 >> Edge(color="purple") >> tooltips
        server_table2 >> Edge(color="purple") >> tooltips
        server_table1 >> Edge(color="purple") >> filter_utils
        server_table2 >> Edge(color="purple") >> filter_utils

        # Data preparation connections
        data_prep >> Edge(label="Pre-compute", color="red") >> fastmap

        # QS files to data_prep
        qs_table1 >> Edge(color="darkgreen") >> data_prep
        qs_table2 >> Edge(color="darkgreen") >> data_prep
        qs_gene >> Edge(color="darkgreen") >> data_prep
        qs_prot >> Edge(color="darkgreen") >> data_prep
        qs_refs >> Edge(color="darkgreen") >> data_prep
        qs_gwas >> Edge(color="darkgreen") >> data_prep

        # CSV to data_prep
        csv_omim >> Edge(color="darkgreen") >> data_prep

        # Fastmap to servers
        fastmap >> Edge(label="O(1) lookup", color="red") >> server_table1
        fastmap >> Edge(color="red") >> server_table2

        # Static assets to UI tabs
        css_js >> Edge(color="gray") >> ui_themes
        phenogram_html >> Edge(label="iframe", color="brown") >> tab_pheno
        python_plot >> Edge(label="iframe", color="brown") >> tab_viz
        tippy >> Edge(color="gray") >> tooltips

        # External APIs to fetch modules (data fetching for ETL)
        ncbi >> Edge(label="fetch_ncbi_gene_data.R", color="navy", style="dashed") >> qs_gene
        uniprot >> Edge(label="fetch_uniprot_data.R", color="navy", style="dashed") >> qs_prot
        pubmed >> Edge(label="fetch_pubmed_data.R", color="navy", style="dashed") >> qs_refs
        omim >> Edge(label="fetch_omim_data.R", color="navy", style="dashed") >> csv_omim

        # Clinical trial registries to table2
        nct >> Edge(color="navy", style="dashed") >> qs_table2
        isrctn >> Edge(color="navy", style="dashed") >> qs_table2
        anzctr >> Edge(color="navy", style="dashed") >> qs_table2
        chictr >> Edge(color="navy", style="dashed") >> qs_table2

        # ETL Pipeline internal flow
        pubmed_search >> Edge(color="teal") >> pdf_retrieval
        pdf_retrieval >> Edge(color="teal") >> llm_extraction
        llm_extraction >> Edge(color="teal") >> validation
        validation >> Edge(color="teal") >> quality_metrics
        quality_metrics >> Edge(color="teal") >> data_merger
        data_merger >> Edge(color="teal") >> database

        # Pipeline orchestration
        pipeline_main >> Edge(label="orchestrates", color="teal", style="bold") >> pubmed_search

        # Pipeline to data files
        database >> Edge(label="updates", color="teal") >> csv_tables

        # Helper package connections
        marco >> Edge(label="helper functions", color="gray", style="dotted") >> data_prep

        # Excel to CSV (source transformation)
        xlsx_source >> Edge(label="source data", color="gray", style="dotted") >> csv_tables


def create_simplified_diagram():
    """
    Creates a simplified high-level architecture diagram.

    This version shows the main layers without internal details,
    suitable for presentations or executive summaries.
    """

    graph_attr = {
        "fontsize": "18",
        "bgcolor": "white",
        "pad": "0.3",
    }

    output_path = os.path.join(SCRIPT_DIR, "svd_dashboard_architecture_simple")

    with Diagram(
        "ICM Cerebral SVD Dashboard - High Level Architecture",
        filename=output_path,
        show=False,
        direction="LR",  # Left to Right
        graph_attr=graph_attr,
        outformat="png",
    ):

        users = Users("Users")

        with Cluster("R Shiny Application"):
            ui = Rack("UI Layer\n(5 Tabs)")
            server = R("Server Logic\n(9 Filter Modules)")
            data_prep = R("Data Preparation")

        with Cluster("Data Storage"):
            data_files = Storage("QS/CSV/XLSX\nData Files")

        with Cluster("External APIs"):
            apis = SQL("NCBI, UniProt,\nPubMed, OMIM,\nCT Registries")

        with Cluster("ETL Pipeline"):
            pipeline = Python("Python\nData Pipeline")

        # Connections
        users >> ui >> server >> data_prep >> data_files
        apis >> Edge(style="dashed") >> data_files
        pipeline >> Edge(label="updates") >> data_files


def create_data_flow_diagram():
    """
    Creates a data flow diagram showing how data moves through the application.

    This diagram focuses on the reactive data flow within the Shiny application,
    from user input through filters to rendered tables.
    """

    graph_attr = {
        "fontsize": "16",
        "bgcolor": "white",
        "pad": "0.3",
        "rankdir": "LR",
    }

    output_path = os.path.join(SCRIPT_DIR, "svd_dashboard_dataflow")

    with Diagram(
        "SVD Dashboard - Reactive Data Flow",
        filename=output_path,
        show=False,
        direction="LR",
        graph_attr=graph_attr,
        outformat="png",
    ):

        # Data Sources
        with Cluster("1. Data Sources"):
            qs_files = Storage("QS Files\n(table1, table2)")
            csv_files = Storage("CSV Files\n(omim_info)")

        # Startup Loading
        with Cluster("2. Startup (app.R)"):
            load_data = R("load_and_prepare_data()")
            precompute = Storage("Pre-computed\nfastmap Indexes")

        # User Interaction
        with Cluster("3. User Input"):
            user_filters = Rack("Filter Checkboxes\n(9 modules)")
            user_search = Rack("Search Input")

        # Reactive Processing
        with Cluster("4. Reactive Processing"):
            filter_reactive = R("Reactive Filtering\n(debounced)")
            data_subset = Storage("Filtered\nData Subset")

        # Output Rendering
        with Cluster("5. Output"):
            datatable = Rack("DT::datatable\n(with tooltips)")
            browser = Users("Browser\nDisplay")

        # Data flow connections
        qs_files >> Edge(label="qs::qread", color="green") >> load_data
        csv_files >> Edge(label="fread", color="green") >> load_data

        load_data >> Edge(label="fastmap", color="blue") >> precompute

        user_filters >> Edge(label="input$filter", color="orange") >> filter_reactive
        user_search >> Edge(label="input$search", color="orange") >> filter_reactive

        precompute >> Edge(label="O(1) lookup", color="red") >> filter_reactive

        filter_reactive >> Edge(color="purple") >> data_subset
        data_subset >> Edge(label="renderDT", color="purple") >> datatable
        datatable >> Edge(color="blue") >> browser


def create_module_hierarchy_diagram():
    """
    Creates a diagram showing the Shiny module hierarchy and relationships.

    This diagram illustrates how the 9 filter module instances are organized
    and how they communicate with the table server logic.
    """

    graph_attr = {
        "fontsize": "14",
        "bgcolor": "white",
        "pad": "0.3",
    }

    output_path = os.path.join(SCRIPT_DIR, "svd_dashboard_modules")

    with Diagram(
        "SVD Dashboard - Shiny Module Hierarchy",
        filename=output_path,
        show=False,
        direction="TB",
        graph_attr=graph_attr,
        outformat="png",
    ):

        # Main Server
        with Cluster("server.R (build_server)"):
            main_server = R("Main Server\nOrchestrator")

            with Cluster("Gene Table (Table 1)"):
                with Cluster("Table 1 Filters"):
                    t1_f1 = R("mr_filter\n(binary)")
                    t1_f2 = R("gwas_trait_filter\n(multi-select)")
                    t1_f3 = R("omics_filter\n(multi-select)")

                t1_server = R("server_table1.R\nbuild_table1_*")
                t1_output = Rack("firstTable\n(DataTable)")

            with Cluster("Clinical Trials (Table 2)"):
                with Cluster("Table 2 Filters"):
                    t2_f1 = R("ge_filter\n(binary)")
                    t2_f2 = R("reg_filter\n(registry)")
                    t2_f3 = R("ct_filter\n(phase)")
                    t2_f4 = R("pop_filter\n(population)")
                    t2_f5 = R("spon_filter\n(sponsor)")
                    t2_f6 = R("sample_size_filter\n(slider)")

                t2_server = R("server_table2.R\nbuild_table2_*")
                t2_output = Rack("secondTable\n(DataTable)")

        # Module UI
        with Cluster("mod_checkbox_filter.R"):
            ui_module = R("checkbox_filter_ui()")
            server_module = R("checkbox_filter_server()\nbinary_checkbox_filter_server()")

        # Connections
        main_server >> Edge(label="moduleServer", color="blue") >> t1_f1
        main_server >> Edge(color="blue") >> t1_f2
        main_server >> Edge(color="blue") >> t1_f3
        main_server >> Edge(color="blue") >> t2_f1
        main_server >> Edge(color="blue") >> t2_f2
        main_server >> Edge(color="blue") >> t2_f3
        main_server >> Edge(color="blue") >> t2_f4
        main_server >> Edge(color="blue") >> t2_f5
        main_server >> Edge(color="blue") >> t2_f6

        t1_f1 >> Edge(color="green") >> t1_server
        t1_f2 >> Edge(color="green") >> t1_server
        t1_f3 >> Edge(color="green") >> t1_server

        t2_f1 >> Edge(color="green") >> t2_server
        t2_f2 >> Edge(color="green") >> t2_server
        t2_f3 >> Edge(color="green") >> t2_server
        t2_f4 >> Edge(color="green") >> t2_server
        t2_f5 >> Edge(color="green") >> t2_server
        t2_f6 >> Edge(color="green") >> t2_server

        t1_server >> Edge(label="renderDT", color="purple") >> t1_output
        t2_server >> Edge(label="renderDT", color="purple") >> t2_output

        ui_module >> Edge(label="UI pattern", color="gray", style="dashed") >> t1_f1
        server_module >> Edge(label="Server pattern", color="gray", style="dashed") >> t1_f1


def main():
    """
    Main function to generate all architecture diagrams.
    """
    print("Generating SVD Dashboard Architecture Diagrams...")
    print(f"Output directory: {SCRIPT_DIR}")
    print("-" * 50)

    print("1. Creating comprehensive architecture diagram...")
    create_architecture_diagram()
    print("   -> svd_dashboard_architecture.png")

    print("2. Creating simplified high-level diagram...")
    create_simplified_diagram()
    print("   -> svd_dashboard_architecture_simple.png")

    print("3. Creating data flow diagram...")
    create_data_flow_diagram()
    print("   -> svd_dashboard_dataflow.png")

    print("4. Creating module hierarchy diagram...")
    create_module_hierarchy_diagram()
    print("   -> svd_dashboard_modules.png")

    print("-" * 50)
    print("Done! All diagrams have been generated.")
    print("\nTo view the diagrams, open the PNG files in the scripts/ directory.")


if __name__ == "__main__":
    main()
