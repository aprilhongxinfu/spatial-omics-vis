"""
CellChat Analysis Module

This module contains functions for analyzing cell-cell communication
through ligand-receptor interactions and spatial adjacency.
"""

import numpy as np
import pandas as pd
from scipy.spatial import Delaunay


def perform_cellchat_analysis(adata):
    """
    Perform cell-cell communication analysis based on spatial adjacency
    and ligand-receptor interactions.
    
    Args:
        adata: AnnData object containing the analysis data
        
    Returns:
        dict: Dictionary containing interaction matrices and scores
    """
    adata_copy = adata.copy()

    cluster_labels = adata_copy.obs["domain"]
    unique_clusters = np.unique(cluster_labels)
    n_clusters = len(unique_clusters)

    # 1. Spatial adjacency statistics
    spatial_coords = adata_copy.obsm['spatial']
    tri = Delaunay(spatial_coords)
    edges = set()
    for simplex in tri.simplices:
        for i in range(len(simplex)):
            for j in range(i + 1, len(simplex)):
                edges.add((simplex[i], simplex[j]))
                edges.add((simplex[j], simplex[i]))

    n_cells = adata_copy.n_obs
    adj_matrix = np.zeros((n_cells, n_cells), dtype=bool)
    for i, j in edges:
        adj_matrix[i, j] = True

    cluster_interaction_matrix = np.zeros((n_clusters, n_clusters))
    cluster_mapping = {cluster: idx for idx, cluster in enumerate(unique_clusters)}

    for i in range(n_cells):
        for j in range(n_cells):
            if adj_matrix[i, j]:
                source_cluster = cluster_labels[i]
                target_cluster = cluster_labels[j]
                source_idx = cluster_mapping[source_cluster]
                target_idx = cluster_mapping[target_cluster]
                cluster_interaction_matrix[source_idx, target_idx] += 1

    # 2. Ligand-receptor pair interaction_score
    # Extended ligand-receptor pair list, including more common cell communication molecules
    lr_pairs = [
        # TGF-β signaling pathway
        ('TGFB1', 'TGFBR1'), ('TGFB1', 'TGFBR2'), ('TGFB2', 'TGFBR1'), ('TGFB2', 'TGFBR2'),
        ('TGFB3', 'TGFBR1'), ('TGFB3', 'TGFBR2'),
        
        # PD-1/PD-L1 signaling pathway
        ('CD274', 'PDCD1'), ('PDCD1LG2', 'PDCD1'),
        
        # Cytokine receptors
        ('IL2', 'IL2RA'), ('IL2', 'IL2RB'), ('IL7', 'IL7R'), ('IL15', 'IL15RA'),
        ('IFNG', 'IFNGR1'), ('IFNG', 'IFNGR2'), ('TNF', 'TNFRSF1A'), ('TNF', 'TNFRSF1B'),
        ('IL1B', 'IL1R1'), ('IL1B', 'IL1R2'), ('IL6', 'IL6R'), ('IL6', 'IL6ST'),
        
        # Chemokines
        ('CXCL12', 'CXCR4'), ('CXCL13', 'CXCR5'), ('CCL2', 'CCR2'), ('CCL5', 'CCR5'),
        ('CXCL8', 'CXCR1'), ('CXCL8', 'CXCR2'),
        
        # Growth factors
        ('VEGFA', 'KDR'), ('VEGFA', 'FLT1'), ('FGF2', 'FGFR1'), ('FGF2', 'FGFR2'),
        ('EGF', 'EGFR'), ('TGFA', 'EGFR'),
        
        # Cell adhesion molecules
        ('CD40LG', 'CD40'), ('CD80', 'CD28'), ('CD86', 'CD28'), ('CD80', 'CTLA4'), ('CD86', 'CTLA4'),
        
        # Other important signaling molecules
        ('WNT1', 'FZD1'), ('WNT3A', 'FZD1'), ('DLL1', 'NOTCH1'), ('DLL4', 'NOTCH1'),
        ('JAG1', 'NOTCH1'), ('JAG2', 'NOTCH1'), ('HGF', 'MET'), ('KITLG', 'KIT')
    ]
    
    all_genes = set(adata_copy.var_names)
    print(f"总基因数: {len(all_genes)}")
    
    # Check gene name matching
    valid_lr_pairs = []
    for ligand, receptor in lr_pairs:
        if ligand in all_genes and receptor in all_genes:
            valid_lr_pairs.append((ligand, receptor))
        else:
            missing = []
            if ligand not in all_genes:
                missing.append(ligand)
            if receptor not in all_genes:
                missing.append(receptor)
            print(f"缺失基因: {missing} (配体-受体对: {ligand}-{receptor})")
    
    print(f"有效配体-受体对数: {len(valid_lr_pairs)}")
    
    # Initialize cluster names for early return case
    clusters = [str(c) for c in unique_clusters]
    n = len(clusters)
    strength_matrix = np.zeros((n, n))
    
    if len(valid_lr_pairs) == 0:
        print("警告：没有找到有效的配体-受体对！")
        # Return empty results
        return {
            "number_matrix": cluster_interaction_matrix.tolist(),
            "strength_matrix": strength_matrix.tolist(),
            "cluster_names": clusters,
            "top_interactions": [],
            "cluster_pair_scores": []
        }

    # Calculate average expression of ligands and receptors in each cluster
    lr_cluster_exp = {}
    for cluster in unique_clusters:
        cells_in_cluster = cluster_labels == cluster
        cluster_expr = adata_copy[cells_in_cluster].X
        if not isinstance(cluster_expr, np.ndarray):
            cluster_expr = cluster_expr.toarray()
        lr_cluster_exp[cluster] = {}
        for gene in set(sum(valid_lr_pairs, ())):
            if gene in all_genes:
                gene_idx = list(adata_copy.var_names).index(gene)
                gene_expr = cluster_expr[:, gene_idx]
                # Calculate mean expression and percentage of expressing cells
                mean_expr = np.mean(gene_expr)
                expr_cells_pct = np.mean(gene_expr > 0)  # Percentage of cells expressing this gene
                lr_cluster_exp[cluster][gene] = {
                    'mean_expr': mean_expr,
                    'expr_cells_pct': expr_cells_pct
                }

    # Set expression threshold filtering
    min_expr_threshold = 0.1  # Minimum mean expression threshold
    min_cells_pct = 0.05      # Minimum expressing cell percentage threshold
    
    print(f"表达过滤阈值: 平均表达 > {min_expr_threshold}, 表达细胞比例 > {min_cells_pct}")
    
    lr_interaction_scores = []
    for source_cluster in unique_clusters:
        for target_cluster in unique_clusters:
            for ligand, receptor in valid_lr_pairs:
                if (ligand in lr_cluster_exp[source_cluster] and 
                    receptor in lr_cluster_exp[target_cluster]):
                    
                    ligand_data = lr_cluster_exp[source_cluster][ligand]
                    receptor_data = lr_cluster_exp[target_cluster][receptor]
                    
                    # Calculate interaction strength: ligand expression × receptor expression
                    interaction_score = ligand_data['mean_expr'] * receptor_data['mean_expr']
                    
                    lr_interaction_scores.append({
                        'source_cluster': str(source_cluster),
                        'target_cluster': str(target_cluster),
                        'ligand': ligand,
                        'receptor': receptor,
                        'ligand_exp': ligand_data['mean_expr'],
                        'receptor_exp': receptor_data['mean_expr'],
                        'ligand_cells_pct': ligand_data['expr_cells_pct'],
                        'receptor_cells_pct': receptor_data['expr_cells_pct'],
                        'interaction_score': interaction_score
                    })
    
    print(f"过滤后的相互作用数量: {len(lr_interaction_scores)}")

    lr_df = pd.DataFrame(lr_interaction_scores)

    # Keep only the maximum interaction_score for each ligand-receptor pair (across cluster combinations)
    lr_df_unique = lr_df.loc[lr_df.groupby(['ligand', 'receptor'])['interaction_score'].idxmax()]

    # Sort by interaction_score, take top 30 different ligand-receptor pairs
    lr_df_unique = lr_df_unique.sort_values('interaction_score', ascending=False)
    top_n = 30
    if len(lr_df_unique) > top_n:
        top_interactions = lr_df_unique.head(top_n)
    else:
        top_interactions = lr_df_unique

    # Cluster name and index mapping
    cluster_idx = {c: i for i, c in enumerate(clusters)}
    strength_matrix = np.zeros((n, n))
    
    # Build strength matrix: sum of all ligand-receptor interaction strengths between each cluster pair
    for _, row in lr_df.iterrows():
        i = cluster_idx[str(row['source_cluster'])]
        j = cluster_idx[str(row['target_cluster'])]
        strength_matrix[i, j] += row['interaction_score']
    
    # Apply logarithmic transformation to strength matrix for better visualization
    if np.max(strength_matrix) > 0:
        # Use log1p transformation to avoid log(0) issues
        strength_matrix = np.log1p(strength_matrix)
        # Round to 3 decimal places
        strength_matrix = np.round(strength_matrix, 3)
        print(f"强度矩阵统计:")
        print(f"  原始最大值: {np.max(np.expm1(strength_matrix)):.4f}")
        print(f"  对数变换后最大值: {np.max(strength_matrix):.3f}")
        print(f"  对数变换后最小值: {np.min(strength_matrix):.3f}")

    cluster_pair_scores = lr_df.groupby(['source_cluster', 'target_cluster'])['interaction_score'].sum().reset_index()

    return {
        "number_matrix": cluster_interaction_matrix.tolist(),
        "strength_matrix": strength_matrix.tolist(),
        "cluster_names": clusters,
        "top_interactions": top_interactions.to_dict(orient="records"),
        "cluster_pair_scores": cluster_pair_scores.to_dict(orient="records")
    }

