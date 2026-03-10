"""
HVG Enrichment Analysis Module

This module contains functions for performing gene enrichment analysis
on highly variable genes and cluster-specific genes.
"""

import scanpy as sc
import pandas as pd
import gseapy as gp
# from HVG_explain import interpret_enrichment_with_llm  # Commented out: AI interpretation disabled


def perform_hvg_enrichment(adata):
    """
    Perform functional enrichment analysis for each cluster in adata.obs['domain']
    
    Args:
        adata: AnnData object containing the analysis data
        
    Returns:
        tuple: (all_clusters_results, all_clusters_results_with_interpret)
    """
    if "highly_variable" not in adata.var:
        return {"error": "Highly variable genes not computed."}, None
    
    # Get all clusters
    if "domain" not in adata.obs:
        return {"error": "Clustering results not found in adata.obs['domain']."}, None
    
    adata.obs["domain"] = adata.obs["domain"].astype(str).astype("category")
    clusters = adata.obs["domain"].cat.categories.tolist()
    
    organism = "Human"
    cutoff = 0.05
    
    # Get available gene sets
    available_sets = gp.get_library_name()
    print([s for s in available_sets if "WikiPathways" in s])
    
    # Specify multiple gene set categories
    gene_sets = {
        "Biological Process": "GO_Biological_Process_2021",
        "Molecular Function": "GO_Molecular_Function_2021",
        "Cellular Component": "GO_Cellular_Component_2021",
        "WikiPathways": "WikiPathways_2024_Human",
        "Reactome": "Reactome_2022"
    }
    
    # Store enrichment results for all clusters
    all_clusters_results = {}
    
    # Perform enrichment analysis for each cluster
    for cluster in clusters:
        print(f"Processing cluster: {cluster}")
        
        # Filter cells for current cluster
        cluster_cells = adata.obs_names[adata.obs["domain"] == cluster]
        
        # Get differentially expressed genes
        cluster = str(cluster)
        sc.tl.rank_genes_groups(adata, groupby='domain', groups=[cluster], reference='rest', method='wilcoxon')
        top_genes = adata.uns['rank_genes_groups']['names'][cluster][:100].tolist()
        if not top_genes:
            all_clusters_results[cluster] = {"error": f"No differentially expressed genes found for cluster {cluster}"}
            continue
        
        all_results = []
        
        for category, gene_set in gene_sets.items():
            try:
                enr = gp.enrichr(
                    gene_list=top_genes,
                    gene_sets=gene_set,
                    organism=organism,
                    outdir=None,
                    cutoff=cutoff,
                )
                df = enr.results.copy()
                
                if not df.empty:
                    df["Gene_set"] = gene_set
                    df["Category"] = category  
                    all_results.append(df)
            except Exception as e:
                print(f"Failed for {cluster}, {gene_set}: {e}")
        
        if not all_results:
            all_clusters_results[cluster] = {"error": f"No enrichment results for cluster {cluster}"}
            continue
        
        merged_df = pd.concat(all_results)
        merged_df = merged_df.sort_values("Adjusted P-value")
        
        top_results = (
            merged_df.groupby("Category", group_keys=False)
            .apply(lambda x: x.head(8))
            .reset_index(drop=True)
            .to_dict(orient="records")
        )
        
        all_clusters_results[cluster] = top_results
    
    # AI interpretation disabled
    # all_clusters_results_with_interpret = interpret_enrichment_with_llm(all_clusters_results)
    # print(all_clusters_results_with_interpret)
    return all_clusters_results, None  # Return None instead of interpreted results


def perform_hvg_enrichment_by_cluster(adata, cluster: str):
    """
    Perform functional enrichment analysis for a specific cluster in adata.obs['domain']
    
    Args:
        adata: AnnData object containing the analysis data
        cluster: Cluster identifier to analyze
        
    Returns:
        dict: Enrichment analysis results for the specified cluster
    """
    if "highly_variable" not in adata.var:
        return {"error": "Highly variable genes not computed."}
    
    # Get all clusters
    if "domain" not in adata.obs:
        return {"error": "Clustering results not found in adata.obs['domain']."}
    
    adata.obs["domain"] = adata.obs["domain"].astype(str).astype("category")
    
    organism = "Human"
    cutoff = 0.05
    
    # Get available gene sets
    available_sets = gp.get_library_name()
    print([s for s in available_sets if "WikiPathways" in s])
    
    # Specify multiple gene set categories
    gene_sets = {
        "Biological Process": "GO_Biological_Process_2021",
        "Molecular Function": "GO_Molecular_Function_2021",
        "Cellular Component": "GO_Cellular_Component_2021",
        "WikiPathways": "WikiPathways_2024_Human",
        "Reactome": "Reactome_2022"
    }
    
    # Store enrichment results
    all_clusters_results = {}
    
    print(f"Processing cluster: {cluster}")
    
    # Filter cells for current cluster
    cluster_cells = adata.obs_names[adata.obs["domain"] == cluster]
    
    # Get differentially expressed genes
    cluster = str(cluster)
    sc.tl.rank_genes_groups(adata, groupby='domain', groups=[cluster], reference='rest', method='wilcoxon')
    top_genes = adata.uns['rank_genes_groups']['names'][cluster][:100].tolist()
    if not top_genes:
        all_clusters_results[cluster] = {"error": f"No differentially expressed genes found for cluster {cluster}"}
        return all_clusters_results
    
    all_results = []
    
    for category, gene_set in gene_sets.items():
        try:
            enr = gp.enrichr(
                gene_list=top_genes,
                gene_sets=gene_set,
                organism=organism,
                outdir=None,
                cutoff=cutoff,
            )
            df = enr.results.copy()
            
            if not df.empty:
                df["Gene_set"] = gene_set
                df["Category"] = category  
                all_results.append(df)
        except Exception as e:
            print(f"Failed for {cluster}, {gene_set}: {e}")
    
    if not all_results:
        all_clusters_results[cluster] = {"error": f"No enrichment results for cluster {cluster}"}
        return all_clusters_results
    
    merged_df = pd.concat(all_results)
    merged_df = merged_df.sort_values("Adjusted P-value")
    
    top_results = (
        merged_df.groupby("Category", group_keys=False)
        .apply(lambda x: x.head(8))
        .reset_index(drop=True)
        .to_dict(orient="records")
    )
    
    all_clusters_results[cluster] = top_results
    
    # AI interpretation disabled
    # all_clusters_results_with_interpret = interpret_enrichment_with_llm(all_clusters_results)
    # print(all_clusters_results_with_interpret)
    return all_clusters_results

