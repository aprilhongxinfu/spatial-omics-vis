# Limit BLAS/OpenMP threads to avoid "NUM_THREADS exceeded", "Bad memory unallocation",
# and malloc heap corruption when mixing OpenBLAS with rpy2/other native libs (must be before numpy)
import os as _os
_env = _os.environ
for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    if _k not in _env:
        _env[_k] = "1"

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.mixture import GaussianMixture, BayesianGaussianMixture
from sklearn.metrics import silhouette_score, adjusted_rand_score, calinski_harabasz_score, davies_bouldin_score
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.neighbors import NearestNeighbors
from scipy.stats import pearsonr, ttest_ind
import scanpy as sc
from typing import Tuple, Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sklearn.neighbors import kneighbors_graph
from scipy.spatial import Delaunay
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.mixture import GaussianMixture, BayesianGaussianMixture
from sklearn.metrics import silhouette_score, adjusted_rand_score, calinski_harabasz_score, davies_bouldin_score
from sklearn.neighbors import NearestNeighbors, kneighbors_graph
from sklearn.cluster import SpectralClustering
from scipy.stats import pearsonr, ttest_ind, mannwhitneyu
from scipy.sparse.csgraph import connected_components
from scipy.spatial import Delaunay
from skimage.morphology import binary_dilation, binary_erosion, disk
import scanpy as sc
from typing import Tuple, Dict, List, Optional
import warnings
import os
import rpy2.robjects as robjects
from rpy2.robjects import numpy2ri
from rpy2.robjects.packages import importr
from rpy2.robjects.conversion import localconverter
import ot

from scipy.spatial import Delaunay
import scipy.sparse as sp

import torch
import igraph as ig
from sklearn.decomposition import PCA
from scipy.spatial.distance import pdist, squareform
from scipy.stats import spearmanr, zscore
import umap
import anndata
from sklearn.utils import check_random_state


def _load_truth_labels(truth_path: str) -> Optional[Dict[str, str]]:
    """加载 ground truth 文件，格式: barcode\\tlabel (每行)。返回 barcode -> label 的字典。"""
    if not truth_path or not os.path.isfile(truth_path):
        return None
    try:
        out = {}
        with open(truth_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    out[parts[0].strip()] = parts[1].strip()
        return out if out else None
    except Exception as e:
        print(f"⚠️ 加载 truth 文件失败 {truth_path}: {e}")
        return None


# truth 与预测标签统一映射到 cluster id：WM=1, Layer_6=2, Layer_5=3, Layer_4=4, Layer_3=5, Layer_2=6, Layer_1=7
TRUTH_LABEL_TO_CLUSTER_ID = {
    "WM": "1",
    "Layer_6": "2",
    "Layer_5": "3",
    "Layer_4": "4",
    "Layer_3": "5",
    "Layer_2": "6",
    "Layer_1": "7",
}


def _label_to_truth_cluster_id(s: str) -> Optional[str]:
    """将 truth/预测标签统一映射到 cluster id (1–7)，无法映射时返回 None。"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    if not s:
        return None
    # 先查表（含 WM / Layer_*）
    key = s if s in TRUTH_LABEL_TO_CLUSTER_ID else None
    if key is None and s.upper() == "WM":
        key = "WM"
    if key is not None:
        return TRUTH_LABEL_TO_CLUSTER_ID.get(key)
    if s.startswith("Layer_"):
        return TRUTH_LABEL_TO_CLUSTER_ID.get(s)
    # 已经是数字 1–7（domain 或 cluster id）
    if s in ("1", "2", "3", "4", "5", "6", "7"):
        return s
    return None


def _accuracy_vs_truth(
    adata,
    region_mask,
    truth_barcode_to_label: Dict[str, str],
    pred_series: pd.Series,
    label: str,
) -> Optional[float]:
    """
    在选中区域内，用 pred_series 与 truth 比较，计算准确率并打印。
    标签统一映射：WM=1, Layer_6=2, Layer_5=3, …, Layer_1=7，只比较可映射的 spot。
    barcode 匹配时做 strip，并明确打印「选中区域总数」与「在 truth 中有标注数」。
    """
    if not truth_barcode_to_label:
        return None
    n_region = int(np.sum(region_mask))
    region_barcodes = list(adata.obs.index[region_mask])
    # 用 strip 后的 barcode 在 truth 中查找，兼容格式差异
    truth_normalized = {k.strip(): v for k, v in truth_barcode_to_label.items()}
    common = []
    truth_raw_list = []
    for b in region_barcodes:
        key = b.strip() if hasattr(b, "strip") else b
        if key in truth_normalized:
            common.append(b)
            truth_raw_list.append(truth_normalized[key])
    if not common:
        print(f"  [truth] 选中区域共 {n_region} 个 spots，与 truth 无交集，跳过 {label} 准确率")
        return None
    n_common = len(common)
    pred = pred_series.reindex(common).astype(str)
    pred_id = [_label_to_truth_cluster_id(p) for p in pred]
    truth_id = [_label_to_truth_cluster_id(t) for t in truth_raw_list]
    valid = [(a, b) for a, b in zip(pred_id, truth_id) if a is not None and b is not None]
    if not valid:
        print(f"  [truth] {label}: 选中区域共 {n_region} 个 spots，其中 {n_common} 个在 truth 中有标注；无有效映射标签，跳过")
        return None
    correct = sum(1 for a, b in valid if a == b)
    n_valid = len(valid)
    acc = correct / n_valid
    print(f"  [truth] {label}: 选中区域共 {n_region} 个 spots，其中 {n_common} 个在 truth 中有标注；准确率（基于该 {n_common} 个）= {acc:.4f} ({correct}/{n_valid}, 映射 WM=1,Layer_6=2,…,Layer_1=7)")
    return acc


def _expand_training_data_by_weight(X: np.ndarray, sample_weight: np.ndarray, max_total: int = 5000) -> np.ndarray:
    """
    scikit-learn's GaussianMixture does NOT support sample_weight in many versions.
    To approximate weighted EM, we upsample rows proportional to weights.

    - X: (n_samples, n_features)
    - sample_weight: (n_samples,) in [0,1] (or any positive scale)
    - max_total: cap to avoid huge expansions
    """
    if sample_weight is None:
        return X
    w = np.asarray(sample_weight, dtype=float).reshape(-1)
    if X.shape[0] != w.shape[0]:
        return X

    w = np.nan_to_num(w, nan=1.0, posinf=1.0, neginf=1.0)
    w = np.clip(w, 1e-6, None)

    # Choose a scale so total repeats stays bounded
    n = X.shape[0]
    target_total = min(max_total, max(n * 5, n))  # allow some expansion but not too large
    scale = max(1.0, target_total / float(np.sum(w)))

    repeats = np.round(w * scale).astype(int)
    repeats[repeats < 1] = 1

    # Hard cap if still too large
    total = int(np.sum(repeats))
    if total > max_total:
        shrink = max_total / float(total)
        repeats = np.maximum(1, np.floor(repeats * shrink).astype(int))

    return np.repeat(X, repeats, axis=0)

def improved_admixture_reclustering(
    adata, 
    region_key: str = 'selected_region', 
    n_components_range: Tuple[int, int] = None,
    use_spatial: bool = True,
    spatial_weight: float = 0.3,
    feature_key: str = "X_pca",
    confidence_threshold: float = 0.7,
    min_cluster_size: int = 20,
    use_hvg: bool = True,
    n_hvg: int = 2000,
    n_components_pca: int = 50,
    mapping_strategy: str = 'feature_functional',
    selection_stability_key: Optional[str] = None,
    stability_protect_threshold: Optional[float] = None,
    stability_train_weight_strength: float = 0.6,
    stability_train_min_weight: float = 0.2,
    truth_path: Optional[str] = None,
    slice_id: Optional[str] = None,
):

    
    print("=== 开始基于特征的Admixture重聚类分析 ===")
    
    # 1. 数据预处理
    region_mask = adata.obs[region_key].astype(bool)
    region_adata = adata[region_mask].copy()
    
    def _copy_obs_matrices(source, target):
        idx = source.obs_names.get_indexer(target.obs_names)
        valid = idx >= 0
        for key, value in source.obsm.items():
            try:
                if isinstance(value, np.ndarray):
                    arr = np.zeros((target.n_obs, value.shape[1]), dtype=value.dtype)
                    if valid.any():
                        arr[valid] = value[idx[valid]]
                    target.obsm[key] = arr
                elif sp.issparse(value):
                    rows = value[idx[valid]] if valid.any() else value[:0]
                    mats = sp.csr_matrix((target.n_obs, value.shape[1]), dtype=value.dtype)
                    if valid.any():
                        mats[valid] = rows
                    target.obsm[key] = mats
                elif isinstance(value, pd.DataFrame):
                    frame = pd.DataFrame(index=target.obs_names, columns=value.columns, dtype=value.values.dtype if hasattr(value.values, 'dtype') else None)
                    if valid.any():
                        frame.iloc[valid] = value.iloc[idx[valid]].values
                    target.obsm[key] = frame.values
                else:
                    subset = value[idx[valid]] if valid.any() else value[:0]
                    target.obsm[key] = subset
            except Exception as err:
                print(f"⚠️ 复制 obsm['{key}'] 时出错: {err}，将跳过复制")
        for key, value in source.obsp.items():
            target.obsp[key] = value

    _copy_obs_matrices(adata, region_adata)

    # 创建扩展区域（包含选择区域的邻近spots，考虑细胞微环境）
    extended_mask = _create_extended_region(adata, region_mask)
    extended_adata = adata[extended_mask].copy()
    _copy_obs_matrices(adata, extended_adata)
    
    print(f"选中区域包含 {region_adata.n_obs} 个spots")
    print(f"扩展区域包含 {extended_adata.n_obs} 个spots (包括原始选择区域)")
    # 添加完整数据集的引用，用于全局原始聚类比较
    region_adata.uns['full_adata_ref'] = adata
    
    # 若提供 ground truth，先计算优化前准确率（当前 domain vs truth）
    truth_map = _load_truth_labels(truth_path) if truth_path else None
    if truth_map is not None:
        print("\n--- Ground Truth 校验 ---")
        before_series = adata.obs["domain"].astype(str) if "domain" in adata.obs.columns else None
        if before_series is not None:
            _accuracy_vs_truth(adata, region_mask, truth_map, before_series, "优化前 (domain)")
    
    # 显示选中区域内的原始聚类分布
    region_clusters = region_adata.obs['domain'].unique()
    print(f"选中区域内的原始聚类类别: {sorted(region_clusters)}")
    
    # 2. 自动确定聚类数量范围
    if n_components_range is None:
        if 'domain' in adata.obs.columns:
            all_original_clusters = adata.obs['domain'].unique()
            n_all_original_clusters = len(all_original_clusters)
            print(f"全数据集包含 {n_all_original_clusters} 个原始聚类: {sorted(all_original_clusters)}")
            
            min_k = 1
            max_k = n_all_original_clusters + 1
            n_components_range = (min_k, max_k)
            
            print(f"自动设置聚类数量范围: {n_components_range}")
        else:
            n_components_range = (1, 6)
            print(f"未找到原始聚类信息，使用默认范围: {n_components_range}")
    else:
        print(f"使用用户指定的聚类数量范围: {n_components_range}")
    
    # 3. 特征选择和预处理
    # X_scaled = _prepare_features(adata, region_adata, use_hvg, n_hvg, feature_key, use_spatial, spatial_weight)
    X_scaled, global_spatial_features = _prepare_features(adata, extended_adata, use_hvg, n_hvg, feature_key, use_spatial, spatial_weight)
    
    # 4. 模型选择
    sample_weight = None
    if selection_stability_key and selection_stability_key in extended_adata.obs.columns:
        try:
            s = pd.to_numeric(extended_adata.obs[selection_stability_key], errors="coerce")
            # If stability not available for a point, keep neutral weight = 1.0
            s = s.fillna(1.0).clip(lower=0.0, upper=1.0)
            strength = float(stability_train_weight_strength)
            strength = max(0.0, min(1.0, strength))
            min_w = float(stability_train_min_weight)
            min_w = max(0.0, min(1.0, min_w))
            # Upweight stable points; downweight unstable points.
            w = (1.0 - strength) + strength * s
            w = w.clip(lower=min_w, upper=1.0)
            sample_weight = w.values.astype(float)
            extended_adata.obs["recluster_train_weight"] = w.values
            print(
                f"使用 {selection_stability_key} 作为训练权重: "
                f"strength={strength}, min_weight={min_w}, "
                f"min={float(np.min(sample_weight)):.3f}, max={float(np.max(sample_weight)):.3f}"
            )
        except Exception as e:
            print(f"⚠️ 生成训练 sample_weight 失败，将忽略稳定度加权: {e}")
            sample_weight = None

    best_model, best_k, evaluation_results = _select_best_model_multi_metric(
        X_scaled, n_components_range, min_cluster_size, extended_adata, global_spatial_features, sample_weight=sample_weight
    )
    
    # 5. 生成聚类结果
    cluster_results = _generate_cluster_results(best_model, X_scaled, best_k, region_adata, extended_adata)
    # print("cluster_results:", cluster_results)
    # 5.5 基于空间近邻投票精修标签（使用默认近邻数 radius=30，与 min_cluster_size 无关）
    cluster_results = _refine_small_clusters(region_adata, cluster_results)
    
    # 6. 基于特征的映射
    mapping_results, feature_signatures = _feature_functional_mapping(
        region_adata, cluster_results, full_adata=adata
    )
    
    # 7. 生物学验证
    bio_validation = _biological_validation(region_adata, cluster_results)
    
    # 8. 整合结果
    adata = _integrate_results_back_improved(
        adata, region_adata, region_mask, cluster_results, mapping_results, bio_validation
    )

    # 若提供了 ground truth，打印按优化结果全部修改后的准确率
    if truth_map is not None and "recluster_result" in adata.obs.columns:
        print("\n--- Ground Truth 校验（整合后）---")
        _accuracy_vs_truth(
            adata, region_mask, truth_map,
            adata.obs["recluster_result"].astype(str),
            "优化后 (recluster_result)",
        )

    # 9.5 可选：参考“选中点稳定度”保护高稳定点（避免轻易被建议改簇）
    if stability_protect_threshold is not None and selection_stability_key:
        adata = _apply_selection_stability_protection(
            adata,
            region_mask=region_mask,
            stability_key=selection_stability_key,
            threshold=float(stability_protect_threshold),
        )
    
    # 8.5 生成重聚类预览图片（ground truth / 当前结果 / 修正后结果）
    if slice_id is not None:
        try:
            from SpatialConsensus_reclustering import generate_recluster_preview_images
            # 从 adata 的 domain 和 recluster_result 构建 correction_results 格式
            region_indices = np.where(region_mask)[0]
            correction_results_for_preview = []
            for idx in region_indices:
                orig = str(adata.obs['domain'].iloc[idx])
                suggested = str(adata.obs['recluster_result'].iloc[idx]) if 'recluster_result' in adata.obs.columns else orig
                correction_results_for_preview.append({
                    'cell_idx': idx,
                    'corrected': orig != suggested,
                    'suggested_label': suggested,
                })
            generate_recluster_preview_images(
                adata,
                selected_mask=region_mask.values if hasattr(region_mask, 'values') else region_mask,
                correction_results=correction_results_for_preview,
                slice_id=slice_id,
                truth_map=truth_map,
                domain_key='domain',
                spatial_key='spatial',
            )
        except Exception as e:
            print(f"⚠️ 生成 Admixture 重聚类预览图片时出错: {e}")

    # 9. 保存详细信息
    adata.uns['recluster_evaluation'] = evaluation_results
    adata.uns['recluster_bio_validation'] = bio_validation
    adata.uns['recluster_feature_signatures'] = feature_signatures
    adata.uns['recluster_mapping'] = mapping_results
    adata.uns['recluster_n_components_range'] = n_components_range
    
    # 保存小聚类重新分配信息
    if 'refined_small_clusters' in cluster_results:
        adata.uns['recluster_refinement'] = {
            'refined_small_clusters': cluster_results['refined_small_clusters'],
            'original_cluster_sizes': cluster_results['original_cluster_sizes'],
            'final_cluster_sizes': cluster_results['final_cluster_sizes'],
            'reassigned_clusters': cluster_results['reassigned_clusters'],
            'min_cluster_size': min_cluster_size
        }
    
    print("=== 重聚类分析完成 ===")
    return adata


def _apply_selection_stability_protection(adata, region_mask, stability_key: str, threshold: float):
    """
    Protect highly-stable selected spots from being marked/assigned as changed.

    Definition of stability (provided by caller):
    - a per-spot scalar in [0,1], where higher means more stable (more consistent across other results)

    Behavior:
    - for selected spots with stability >= threshold, force recluster_result back to original domain
      and mark label_changed = False.
    """
    try:
        if stability_key not in adata.obs.columns:
            return adata
        if 'domain' not in adata.obs.columns:
            return adata

        stable = pd.to_numeric(adata.obs[stability_key], errors="coerce").fillna(-1.0)
        stable_mask = pd.Series(region_mask, index=adata.obs.index).astype(bool) & (stable >= threshold)

        n_protected = int(stable_mask.sum())
        if n_protected <= 0:
            return adata

        original_domain = adata.obs['domain'].astype(str)
        adata.obs.loc[stable_mask, 'recluster_result'] = original_domain.loc[stable_mask]
        adata.obs.loc[stable_mask, 'label_changed'] = False
        adata.obs.loc[stable_mask, 'recluster_mapping_source'] = 'stability_protected'
        adata.obs.loc[stable_mask, 'recluster_relationship'] = 'stability_protected'
        # Keep confidence conservative but indicate we intentionally protected
        adata.obs.loc[stable_mask, 'recluster_mapping_confidence'] = 1.0

        adata.obs['recluster_protected_by_stability'] = False
        adata.obs.loc[stable_mask, 'recluster_protected_by_stability'] = True

        adata.uns['selection_stability_protection'] = {
            'enabled': True,
            'stability_key': stability_key,
            'threshold': threshold,
            'protected_count': n_protected,
        }
        return adata
    except Exception as e:
        print(f"⚠️ 稳定度保护失败: {e}")
        return adata


def _create_extended_region(adata, region_mask, extension_radius=3):
    """创建扩展区域，包含选择区域周围的spots，模拟细胞微环境"""
    # 如果没有空间信息，返回原始区域
    if 'spatial' not in adata.obsm:
        return region_mask
    
    # 获取空间坐标
    coords = adata.obsm['spatial']
    region_coords = coords[region_mask]
    
    # 使用KNN找到扩展区域
    n_neighbors = min(extension_radius * 10, adata.n_obs - 1)
    nbrs = NearestNeighbors(n_neighbors=n_neighbors, metric='euclidean').fit(coords)
    
    # 为每个选中的spot找到邻居
    extended_indices = set()
    for coord in region_coords:
        distances, indices = nbrs.kneighbors(coord.reshape(1, -1))
        # 只添加一定半径内的邻居
        for dist, idx in zip(distances[0], indices[0]):
            if dist <= extension_radius * np.mean(pdist(coords, 'euclidean')) / 5:
                extended_indices.add(idx)
    
    # 创建扩展掩码
    extended_mask = np.zeros(adata.n_obs, dtype=bool)
    extended_mask[list(extended_indices)] = True
    
    # 确保原始区域包含在扩展区域内
    extended_mask = extended_mask | region_mask
    
    return extended_mask



def _prepare_features(adata, extended_adata, use_hvg, n_hvg, feature_key, use_spatial, spatial_weight):
    """准备聚类特征，优先使用降维结果以加快速度"""
    
    region_adata = extended_adata
    print("region_adata:", region_adata)
    adata_var = adata

    # 优先使用已有的降维特征
    X_emb = None
    if feature_key is not None and feature_key in region_adata.obsm:
        X_emb = region_adata.obsm[feature_key].copy()
        print(f"使用 {feature_key} 降维特征，维度: {X_emb.shape}")
    else:
        print(f"⚠️ 未找到 feature_key={feature_key}，自动计算 PCA 作为替代")
        temp = region_adata.copy()
        sc.pp.normalize_total(temp, target_sum=1e4)
        sc.pp.log1p(temp)
        sc.pp.pca(temp, n_comps=min(50, temp.n_obs - 1, temp.n_vars))
        X_emb = temp.obsm['X_pca']
        region_adata.obsm['X_pca_fallback'] = X_emb
        print(f"使用 PCA 降维特征，维度: {X_emb.shape}")
    
    # 选择高变基因
    if use_hvg and 'highly_variable' in region_adata.var.columns:
        hvg_mask = region_adata.var['highly_variable']
        if hvg_mask.sum() > n_hvg:
            if 'dispersions' in region_adata.var.columns:
                hvg_genes = region_adata.var[hvg_mask].sort_values('dispersions', ascending=False).head(n_hvg).index
            elif 'dispersions_norm' in region_adata.var.columns:
                hvg_genes = region_adata.var[hvg_mask].sort_values('dispersions_norm', ascending=False).head(n_hvg).index
            else:
                print("未找到 dispersions 列，计算基因变异来选择高变基因...")
                hvg_data = region_adata[:, hvg_mask]
                X_hvg = hvg_data.X
                if hasattr(X_hvg, 'toarray'):
                    X_hvg = X_hvg.toarray()
                gene_vars = np.var(X_hvg, axis=0)
                top_var_indices = np.argsort(gene_vars)[-n_hvg:]
                hvg_genes = hvg_data.var.index[top_var_indices]
        else:
            hvg_genes = region_adata.var[hvg_mask].index
        
        # 安全地索引基因，处理重复基因名问题
        try:
            X_hvg = region_adata[:, hvg_genes].X
            if hasattr(X_hvg, 'toarray'):
                X_hvg = X_hvg.toarray()
            print(f"选择了 {len(hvg_genes)} 个高变基因")
        except Exception as index_error:
            print(f"⚠️ 基因索引出错: {index_error}")
            print("🔧 尝试修复基因名重复问题...")
            
            # 确保基因名唯一
            if not region_adata.var_names.is_unique:
                region_adata.var_names_make_unique()
                print("✅ 基因名已修复为唯一")
            
            # 重新尝试索引
            hvg_genes = hvg_genes[hvg_genes.isin(region_adata.var_names)]
            X_hvg = region_adata[:, hvg_genes].X
            if hasattr(X_hvg, 'toarray'):
                X_hvg = X_hvg.toarray()
            print(f"修复后选择了 {len(hvg_genes)} 个高变基因")
            
    else:
        print("⚠️ 未使用 HVG 过滤，改为直接使用全部基因")
        hvg_genes = region_adata.var_names
        X_hvg = region_adata.X
        if hasattr(X_hvg, 'toarray'):
            X_hvg = X_hvg.toarray()
    
    if use_spatial and 'spatial' in adata.obsm:
        # 计算全局空间背景
        all_coords = adata.obsm['spatial']
        region_coords = region_adata.obsm['spatial']
        
        # 使用Delaunay三角剖分找到空间邻居
        tri = Delaunay(all_coords)
        simplex_indices = tri.find_simplex(region_coords)

        # 计算周围环境的特征
        context_features = []
        for i, simplex_idx in enumerate(simplex_indices):
            if simplex_idx == -1:
                # 对于边界点，使用最近邻
                distances = np.linalg.norm(all_coords - region_coords[i], axis=1)
                neighbor_indices = np.argsort(distances)[:20]
            else:
                # 获取Delaunay邻居
                simplex = tri.simplices[simplex_idx]
                neighbor_indices = simplex
 
 
            neighbor_expr = adata_var.X[neighbor_indices]
            if hasattr(neighbor_expr, 'toarray'):
                neighbor_expr = neighbor_expr.toarray()
            
            # 计算多种统计量作为上下文特征
            mean_expr = np.mean(neighbor_expr, axis=0)    ##获取邻居的所有HVG的表达量均值
            std_expr = np.std(neighbor_expr, axis=0)      ##获取邻居的所有HVG的表达量标准差
         
            context_features.append(np.concatenate([mean_expr, std_expr]))
            
        context_features = np.array(context_features)
        print("context_features.shape:", context_features.shape)
   
        # 标准化并加权
        context_scaled = StandardScaler().fit_transform(context_features) * spatial_weight
        global_spatial_features = context_scaled

    scaler = StandardScaler()
    X_hvg_scaled = scaler.fit_transform(X_hvg)
    X_emb_scaled = scaler.fit_transform(X_emb)
    X_scaled = np.hstack([X_hvg_scaled, X_emb_scaled, context_scaled])
    print("X_scaled.shape:", X_scaled.shape)
    # PCA降维到50维（或更少，如果样本数不够）
    n_components_pca = min(50, X_hvg_scaled.shape[0] - 1, X_hvg_scaled.shape[1])
    pca = PCA(n_components=n_components_pca, random_state=42)
    X = pca.fit_transform(X_scaled)
    
    print(f"PCA降维后特征维度: {X.shape}")
    print(f"PCA解释的方差比例: {pca.explained_variance_ratio_.sum():.3f}")
    
    # 保存PCA结果到region_adata中，供后续映射使用
    region_adata.obsm['X_pca_recluster'] = X
    region_adata.uns['pca_recluster'] = {
        'pca_model': pca,
        'scaler': scaler,
        'hvg_genes': hvg_genes.tolist(),
        'explained_variance_ratio': pca.explained_variance_ratio_
    }
    
    # 标准化特征（如果还没有标准化）
    if X.std() > 1e-6:  # 检查是否已经标准化
        scaler_final = StandardScaler()
        X_scaled = scaler_final.fit_transform(X)
        print("对特征进行了最终标准化")
    else:
        X_scaled = X
        print("特征已经标准化，跳过")
    
    return X_scaled, global_spatial_features



def _calculate_spatial_context(adata, extended_adata, extended_mask, gene_modules, spatial_weight):
    """计算空间上下文特征，考虑组织微环境"""
    
    # 获取空间坐标
    all_coords = adata.obsm['spatial']
    region_coords = extended_adata.obsm['spatial']
    
    # 1. 基于Delaunay三角剖分的微环境构建
    context_features = []
    
    # 使用KNN找到每个点的空间邻居
    n_neighbors = min(15, adata.n_obs - 1)
    nbrs = NearestNeighbors(n_neighbors=n_neighbors, metric='euclidean').fit(all_coords)
    distances, indices = nbrs.kneighbors(region_coords)
    
    # 构建包含HVG的数据视图
    if 'highly_variable' in adata.var:
        hvg_mask = adata.var['highly_variable']
        adata_hvg = adata[:, hvg_mask].copy()
    else:
        adata_hvg = adata.copy()
    
    # 为每个spot计算空间上下文特征
    for spot_idx in range(extended_adata.n_obs):
        # 获取当前spot的邻居
        neighbors = indices[spot_idx][1:]  # 排除自身
        
        # 获取邻居的表达数据
        if isinstance(adata_hvg.X, np.ndarray):
            neighbor_expr = adata_hvg.X[neighbors]
        else:
            neighbor_expr = adata_hvg.X[neighbors].toarray()
        
        # 计算距离加权的空间特征
        weights = 1.0 / (distances[spot_idx][1:] + 1e-10)
        weights = weights / np.sum(weights)
        
        # 多尺度特征
        # 1. 近邻平均表达（反映直接微环境）
        mean_expr = np.average(neighbor_expr, weights=weights, axis=0)
        
        # 2. 表达梯度（反映空间变化趋势）
        weighted_diffs = np.zeros_like(mean_expr)
        center_expr = adata_hvg.X[indices[spot_idx][0]]
        if not isinstance(center_expr, np.ndarray):
            center_expr = center_expr.toarray().flatten()
        
        for n_idx, weight in enumerate(weights):
            n_expr = neighbor_expr[n_idx]
            weighted_diffs += weight * (n_expr - center_expr)
        
        # 3. 空间异质性（表达变异系数）
        variation = np.std(neighbor_expr, axis=0) / (np.mean(neighbor_expr, axis=0) + 1e-10)
        
        # 将特征连接起来
        spot_context = np.concatenate([mean_expr, weighted_diffs, variation])
        context_features.append(spot_context)
    
    # 2. 转换为模块水平的空间特征
    context_features = np.array(context_features)
    
  
   
        # 如果没有模块信息，直接返回标准化的上下文特征
    scaler = StandardScaler()
    return scaler.fit_transform(context_features) * spatial_weight
    


def _select_best_model_multi_metric(X_scaled, n_components_range, min_cluster_size, region_adata, global_spatial_features, sample_weight=None):

    n_spots, n_features = X_scaled.shape
    evaluation_results = []
    best_score = -float('inf') 
    best_k = n_components_range[0]
    best_model = None
    use_spatial = global_spatial_features is not None
    if use_spatial:
        # 使用全局空间上下文特征构建多尺度邻接图
        n_samples = X_scaled.shape[0]
        n_neighbors = min(15, n_samples-1)
        
        # 构建多尺度空间图
        spatial_graphs = []
        # for scale in [3,6,12,15]:
        #     nbrs = NearestNeighbors(n_neighbors=min(scale, n_samples-1)).fit(global_spatial_features) 
        #     graph = nbrs.kneighbors_graph(global_spatial_features, mode='connectivity')
        #     spatial_graphs.append(graph)

        for k in [5, 8, 10]:  # 替换原尺度列表，直接用邻域数
            if k >= n_samples:
                k = n_samples - 1  # 避免超过样本数
            nbrs = NearestNeighbors(n_neighbors=k).fit(global_spatial_features)
            graph = nbrs.kneighbors_graph(global_spatial_features, mode='connectivity')  # 0-1邻接矩阵
            spatial_graphs.append(graph)

        # 合并多尺度图
        spatial_graph = sum(spatial_graphs) / len(spatial_graphs)



    for k in range(n_components_range[0], n_components_range[1] + 1):
        print(f"\n评估 K={k}...")
        
        # 多次初始化确保稳定性
        k_results = []
        
        for init_seed in [42, 123, 456, 789, 999]:  # 5次不同初始化
            try:
                gmm = GaussianMixture(
                    n_components=k, 
                    random_state=init_seed,
                    covariance_type='full', 
                    max_iter=200,
                    n_init=3
                )
                X_train = _expand_training_data_by_weight(X_scaled, sample_weight)
                gmm.fit(X_train)
                
                if not gmm.converged_:
                    continue
                    
                labels = gmm.predict(X_scaled)
                unique_labels = np.unique(labels)
    
                # if len(unique_labels) == 0:
                #     continue
                metrics = _calculate_clustering_metrics_with_context(
                    X_scaled, labels, "EEE", spatial_graph
                )

                sil_score = metrics['silhouette_score']
                calinski_harabasz = metrics['calinski_harabasz']
                db_score = metrics['davies_bouldin_inv']
                spatial_score = metrics['spatial_continuity'] if use_spatial else 0
                print("spatial_score:", spatial_score)
                boundary_consistency = metrics['boundary_consistency'] if use_spatial else 0
                print("boundary_consistency:", boundary_consistency)

                stability_score = _evaluate_cluster_stability(X_scaled, gmm, labels)
                
                # 聚类平衡性
                cluster_sizes = np.bincount(labels)
                size_balance = 1.0 - np.std(cluster_sizes) / np.mean(cluster_sizes)
                size_balance = np.clip(size_balance, 0, 1)
                
                # 最小聚类大小检查
                min_size_penalty = 1.0 if np.min(cluster_sizes) >= min_cluster_size else 0.5
                
                # 信息准则
                bic = gmm.bic(X_scaled)
                aic = gmm.aic(X_scaled)
                
                k_results.append({
                    'silhouette': sil_score,
                    'spatial_continuity': spatial_score,
                    'boundary_consistency': boundary_consistency,
                    'davies_bouldin_inv': db_score,
                    'calinski_harabasz': calinski_harabasz,
                    'stability': stability_score,
                    'size_balance': size_balance,
                    'min_size_penalty': min_size_penalty,
                    'bic': bic,
                    'aic': aic,
                    'model': gmm
                })
                # print("k_results:", k_results)
                
            except Exception as e:
                print(f"    初始化 {init_seed} 失败: {e}")
                continue
        
        if not k_results:
            print(f"  K={k}: 所有初始化都失败")
            continue
        
        # 计算稳定性指标
        sil_scores = [r['silhouette'] for r in k_results]
        calinski_harabasz_scores = [r['calinski_harabasz'] for r in k_results]
        db_scores = [r['davies_bouldin_inv'] for r in k_results]
        spatial_continuity_scores = [r['spatial_continuity'] for r in k_results]
        boundary_consistency_scores = [r['boundary_consistency'] for r in k_results]



        mean_sil = np.mean(sil_scores)
        std_sil = np.std(sil_scores)
        mean_calinski_harabasz = np.mean(calinski_harabasz_scores)
        mean_db = np.mean(db_scores)
        mean_spatial = np.mean(spatial_continuity_scores)
        mean_spatial_boundary = np.mean(boundary_consistency_scores)
        
        # 选择该 K 下最佳初始化（用于后续取 model）
        best_init_idx = np.argmax(sil_scores)
        best_init_result = k_results[best_init_idx]
        
        # 仅记录原始指标，归一化与综合评分在全部 K 评估完后按「本次 K 范围内 min-max」统一计算
        result = {
            'n_components': k,
            'mean_silhouette': mean_sil,
            'mean_calinski_harabasz': mean_calinski_harabasz,
            'mean_db': mean_db,
            'mean_spatial_continuity': mean_spatial,
            'mean_spatial_boundary': mean_spatial_boundary,
            'bic': best_init_result['bic'],
            'aic': best_init_result['aic'],
            'size_balance': best_init_result['size_balance'],
            'min_size_penalty': best_init_result['min_size_penalty'],
            'stability': 1.0 - mean_sil,
            'n_successful_inits': len(k_results),
            'method': 'multi_metric',
            'best_init_result': best_init_result,
        }
        evaluation_results.append(result)
        
        print(f"  K={k}: 轮廓={mean_sil:.3f}, CH={mean_calinski_harabasz:.1f}, db_inv={mean_db:.3f}, "
              f"空间连续性={mean_spatial:.3f}, 边界一致性={mean_spatial_boundary:.3f}")
    
    if not evaluation_results:
        print("所有配置都失败，使用备用方案")
        return _fallback_simple_selection(X_scaled, n_components_range, sample_weight=sample_weight)
    
    # 在本次 K 范围内对每个指标做 min-max 归一化，再加权得到综合分（避免 CH 等尺度差异导致区分度丧失）
    def _min_max_norm(arr):
        a = np.asarray(arr, dtype=float)
        lo, hi = np.nanmin(a), np.nanmax(a)
        if hi <= lo:
            return np.ones_like(a) * 0.5
        return (a - lo) / (hi - lo)
    
    sil_arr = np.array([r['mean_silhouette'] for r in evaluation_results])
    ch_arr = np.array([r['mean_calinski_harabasz'] for r in evaluation_results])
    db_arr = np.array([r['mean_db'] for r in evaluation_results])
    spatial_arr = np.array([r['mean_spatial_continuity'] for r in evaluation_results])
    boundary_arr = np.array([r['mean_spatial_boundary'] for r in evaluation_results])
    bic_arr = np.array([r['bic'] for r in evaluation_results])
    balance_arr = np.array([r['size_balance'] for r in evaluation_results])
    
    n_sil = _min_max_norm(sil_arr)
    n_ch = _min_max_norm(ch_arr)
    n_db = _min_max_norm(db_arr)
    n_spatial = _min_max_norm(spatial_arr)
    n_boundary = _min_max_norm(boundary_arr)
    # BIC 越小越好，归一化后取 1 - norm 使越小 BIC 得分越高
    n_bic = 1.0 - _min_max_norm(bic_arr)
    n_balance = _min_max_norm(balance_arr)
    
    for i, r in enumerate(evaluation_results):
        pen = r['min_size_penalty']
        r['combined_score'] = (
            n_sil[i] * 0.5 +
            n_ch[i] * 0.15 +
            n_db[i] * 0.15 +
            n_spatial[i] * 0.2 +
            n_boundary[i] * 0.15 +
            n_bic[i] * 0.15 +
            n_balance[i] * 0.10
        ) * pen
    
    best_result = max(evaluation_results, key=lambda x: x['combined_score'])
    best_score = best_result['combined_score']
    best_k = best_result['n_components']
    best_model = best_result['best_init_result']['model']
    
    for r in evaluation_results:
        r.pop('best_init_result', None)
    
    cv_df = pd.DataFrame(evaluation_results)
    print(f"\n=== 多指标评估结果 ===")
    if not cv_df.empty:
        print(cv_df[['n_components', 'combined_score', 'mean_silhouette', 'mean_calinski_harabasz', 'mean_db', 
                    'mean_spatial_continuity', 'mean_spatial_boundary', 'stability', 'n_successful_inits']])
    
    print(f"\n选择最佳K: {best_k} (综合评分: {best_score:.3f})")
    
    return best_model, best_k, cv_df


def _calculate_clustering_metrics_with_context(X, labels, model, spatial_graph=None, 
                                             adata=None, region_mask=None, original_labels=None):
    
    metrics = {}
    
    # 1. 基础聚类指标（仅基于圈内spots）
    if len(np.unique(labels)) >= 1:
        metrics['silhouette_score'] = silhouette_score(X, labels)
        metrics['calinski_harabasz'] = calinski_harabasz_score(X, labels)
        db_score = davies_bouldin_score(X, labels)
        metrics['davies_bouldin_inv'] = 1 / (1 + db_score)
    else:
        metrics.update({
            'silhouette_score': -1,
            'calinski_harabasz': 0,
            'davies_bouldin_inv': 0
        })
    
    # 2. 模型评分
    if hasattr(model, 'score'):
        metrics['log_likelihood'] = model.score(X)
    
    # 3. 空间指标（考虑周围spots）
    if spatial_graph is not None:
        # 3.1 圈内spots的空间连续性
        metrics['spatial_continuity'] = _calculate_spatial_continuity(labels, spatial_graph)
        
        # 3.2 边界一致性（考虑圈内spots之间的关系）
        metrics['boundary_consistency'] = _calculate_boundary_consistency(labels, spatial_graph)
    
    
    return metrics


def _calculate_spatial_continuity(labels, spatial_graph):
    """计算空间连续性得分"""
    from sklearn.metrics import adjusted_rand_score
    
    # 基于空间图的连通分量
    # print("spatial_graph:", spatial_graph)
    n_components, pred_labels = connected_components(spatial_graph)
    print("n_components:", n_components)
    # print("pred_labels:", pred_labels)
    # 计算ARI
    if n_components > 1:
        ari = adjusted_rand_score(labels, pred_labels)
    else:
        # 如果完全连通，使用局部一致性
        n_samples = len(labels)
        consistency_scores = []
        for i in range(n_samples):
            neighbors = spatial_graph[i].nonzero()[1]
            if len(neighbors) > 0:
                same_label = np.sum(labels[neighbors] == labels[i])
                consistency_scores.append(same_label / len(neighbors))
        ari = np.mean(consistency_scores) if consistency_scores else 0
    print("ari:", ari)
    
    return ari

def _calculate_boundary_consistency(labels, spatial_graph):
    """计算边界一致性得分"""
    n_samples = len(labels)
    boundary_scores = []
    
    for i in range(n_samples):
        neighbors = spatial_graph[i].nonzero()[1]
        if len(neighbors) > 0:
            # 计算边界分数 (1 - 同标签邻居比例)``
            same_label = np.sum(labels[neighbors] == labels[i])
            boundary_score = 1 - (same_label / len(neighbors))
            boundary_scores.append(boundary_score)

    # 好的聚类应该有清晰的边界，所以边界分数应该适中
    print("boundary_scores:", (1 - np.mean(boundary_scores)))
    return 1 - np.mean(boundary_scores) if boundary_scores else 0


def _morans_I(x, coords, k=6):
    import numpy as np
    from sklearn.neighbors import kneighbors_graph
    n = len(x)
    x = np.asarray(x)
    x_mean = np.mean(x)
    x_dev = x - x_mean
    W = kneighbors_graph(coords, k, mode='connectivity', include_self=False)
    W = W.toarray()
    W = np.maximum(W, W.T)
    w_sum = W.sum()
    if w_sum == 0:
        return np.nan
    num = 0.0
    for i in range(n):
        for j in range(n):
            num += W[i, j] * x_dev[i] * x_dev[j]
    denom = np.sum(x_dev ** 2)
    if denom == 0:
        return np.nan
    I = n / w_sum * num / denom
    return I

def _evaluate_spatial_continuity(labels, region_adata):
    """评估聚类的空间连续性（Moran's I）"""
    if region_adata is None or 'spatial' not in region_adata.obsm:
        return 0.5
    try:
        spatial_coords = region_adata.obsm['spatial']
        unique_labels = np.unique(labels)
        morans_scores = []
        for label in unique_labels:
            # 对每个聚类标签做one-hot编码
            onehot = (labels == label).astype(float)
            I = _morans_I(onehot, spatial_coords, k=6)
            if not np.isnan(I):
                # 归一化到0~1区间，I理论最大值为1，最小值为-1
                norm_I = (I + 1) / 2
                morans_scores.append(norm_I)
        if morans_scores:
            return float(np.mean(morans_scores))
        else:
            return 0.5
    except Exception as e:
        print(f"空间连续性（Moran's I）评估失败: {e}")
        return 0.5

def _evaluate_cluster_stability(X_subset, gmm_model, labels):
    """评估聚类稳定性"""
    
    try:
        # 计算每个点的聚类概率
        probs = gmm_model.predict_proba(X_subset)
        max_probs = np.max(probs, axis=1)
        
        # 稳定性定义为高置信度分配的比例
        high_confidence_ratio = np.mean(max_probs > 0.7)
        
        # 考虑聚类大小的平衡性
        unique_labels, counts = np.unique(labels, return_counts=True)
        size_balance = 1.0 - np.std(counts) / np.mean(counts) if len(counts) > 1 else 1.0
        size_balance = np.clip(size_balance, 0, 1)
        
        # 综合稳定性分数
        stability_score = high_confidence_ratio * 0.7 + size_balance * 0.3
        
        return stability_score
        
    except Exception as e:
        print(f"稳定性评估失败: {e}")
        return 0.5

def _generate_cluster_results(best_model, X_scaled, best_k, region_adata, extended_adata):
    """生成聚类结果"""

    # 对所有extended_adata的spot进行预测
    q_matrix = best_model.predict_proba(X_scaled)
    hard_labels = best_model.predict(X_scaled)
    max_probs = np.max(q_matrix, axis=1)
    
    # 获取region_adata在extended_adata中的索引
    # 假设region_adata的spot是extended_adata的子集
    # 可以通过spot的标识（比如坐标或ID）找到对应关系
    region_indices = []
    
    # 假设两个AnnData对象都有相同的索引标识
    for idx in region_adata.obs.index:
        if idx in extended_adata.obs.index:
            # print("idx:", idx)
            region_indices.append(extended_adata.obs.index.get_loc(idx))
    
    # 只保留region_adata对应的结果
    region_hard_labels = hard_labels[region_indices]
    region_q_matrix = q_matrix[region_indices]
    region_max_probs = max_probs[region_indices]
    
    results = {
        'hard_labels': region_hard_labels,
        'q_matrix': region_q_matrix,
        'max_probs': region_max_probs,
        'n_components': best_k
    }
    
    return results

    
    return results

def _refine_small_clusters(region_adata, cluster_results, radius=30):
    """使用空间近邻投票机制对聚类标签进行精修。

    Args:
        region_adata: 区域 AnnData。
        cluster_results: 聚类结果（含 hard_labels, q_matrix 等）。
        radius: 用于投票的近邻数量（取每个细胞最近的 radius 个邻居做多数投票），与 min_cluster_size 无关，默认 30。
    """
    
    print("开始基于空间近邻进行标签精修...")
    
    hard_labels = cluster_results['hard_labels'].copy()
    q_matrix = cluster_results['q_matrix'].copy()
    n_components = cluster_results['n_components']
    
    # 确保有空间坐标
    if 'spatial' not in region_adata.obsm:
        print("错误：需要空间坐标信息进行近邻标签精修")
        return cluster_results
    
    spatial_coords = region_adata.obsm['spatial']
    n_cells = spatial_coords.shape[0]
    
    # 计算细胞间的空间距离矩阵
    import scipy.spatial.distance as dist
    distance_matrix = dist.squareform(dist.pdist(spatial_coords, 'euclidean'))
    
    # 记录修改的标签数量
    changes_count = 0
    old_labels = hard_labels.copy()
    print("old_labels.shape:", old_labels.shape)
    new_labels = hard_labels.copy()
    
    # 对每个细胞进行标签精修
    for cell_idx in range(n_cells):
        # 获取当前细胞的距离向量
        dist_vec = distance_matrix[cell_idx, :]
        
        # 获取最近的radius个邻居的索引（不包括自己）
        neighbor_indices = np.argsort(dist_vec)[1:radius+1]
        
        # 获取这些邻居的标签
        neighbor_labels = hard_labels[neighbor_indices]
        
        # 使用投票机制确定新标签（出现最多次的标签）
        from collections import Counter
        label_counts = Counter(neighbor_labels)
        
        if label_counts:
            majority_label = label_counts.most_common(1)[0][0]
            
            # 只有当新标签与原标签不同时才更新
            if majority_label != old_labels[cell_idx]:
                new_labels[cell_idx] = majority_label
                
                # 更新概率矩阵
                q_matrix[cell_idx, :] = 0.1 / (n_components - 1)
                q_matrix[cell_idx, majority_label] = 0.9
                
                changes_count += 1
    
    # 更新最终标签
    hard_labels = new_labels
    
    print(f"标签精修完成：共修改了 {changes_count} 个细胞的标签 ({changes_count/n_cells*100:.2f}%)")
    
    # 更新聚类结果
    updated_results = {
        'hard_labels': hard_labels,
        'q_matrix': q_matrix,
        'max_probs': np.max(q_matrix, axis=1),
        'n_components': n_components,
        'spatially_refined': True,
        'refinement_radius': radius,
        'labels_changed': changes_count
    }
    
    return updated_results


# def _feature_functional_mapping(region_adata, cluster_results, full_adata):
#     """基于基因表达特征相似性的映射"""
    
#     print("开始基于基因表达特征相似性的映射...")
    
#     hard_labels = cluster_results['hard_labels']
#     q_matrix = cluster_results['q_matrix']
#     n_components = cluster_results['n_components']
    
#     mapping_results = {}
#     feature_signatures = {}
    
#     # 获取全数据集的原始聚类信息
#     all_original_clusters = full_adata.obs['domain'].unique()
#     print(f"全数据集包含的原始聚类: {sorted(all_original_clusters)}")
    
#     # 计算全数据集中每个原始聚类的基因表达特征
#     print("计算原始聚类的基因表达特征...")
#     original_cluster_features = _compute_original_cluster_features_with_deg(full_adata, all_original_clusters)
    
#     # 计算所有新聚类的特征
#     print("计算新聚类的基因表达特征...")
#     all_new_cluster_features = _compute_new_cluster_features_with_deg(region_adata, cluster_results)
    
#     for i in range(n_components):
#         cluster_mask = hard_labels == i
#         cluster_size = np.sum(cluster_mask)
        
#         if cluster_size == 0:
#             print(f"聚类 {i}: 无数据，跳过")
#             continue
        
#         print(f"\n处理聚类 {i} 的 {cluster_size} spots")
        
#         # 获取当前聚类的特征
#         if i in all_new_cluster_features:
#             new_cluster_features = all_new_cluster_features[i]
#         else:
#             new_cluster_features = {'method': 'empty', 'signature': None}
        
#         feature_signatures[i] = new_cluster_features
        
#         # 基于基因表达特征相似性进行映射
#         mapping_result = _map_by_deg_similarity(
#             region_adata, cluster_mask, new_cluster_features, 
#             original_cluster_features, all_original_clusters
#         )
#         mapping_results[i] = mapping_result
    
#     print("基于基因表达特征相似性的映射完成")
#     return mapping_results, feature_signatures

def _compute_original_cluster_features_with_deg(full_adata, all_original_clusters):
    """使用差异表达分析计算原始聚类的特征基因"""
    
    print("使用scanpy差异表达分析计算原始聚类特征...")
    
    # 创建一个副本用于差异表达分析
    adata_deg = full_adata.copy()
    
    # 确保数据格式正确
    if hasattr(adata_deg.X, 'toarray'):
        adata_deg.X = adata_deg.X.toarray()
    
    # 运行差异表达分析
    print("  运行差异表达分析...")
    try:
        sc.tl.rank_genes_groups(
            adata_deg, 
            groupby='domain',
            method='wilcoxon',  # 使用Wilcoxon秩和检验
            key_added='rank_genes_groups',
            n_genes=200,  # 每个组最多200个差异基因
            use_raw=False
        )
        print("  差异表达分析完成")
    except Exception as e:
        print(f"  差异表达分析失败: {e}")
        # 如果失败，回退到原来的方法
        return _compute_original_cluster_features_fallback(full_adata, all_original_clusters)
    
    # 提取每个聚类的差异表达基因
    original_features = {}
    
    # 获取差异表达结果
    deg_results = adata_deg.uns['rank_genes_groups']
    
    for cluster_id in all_original_clusters:
        if cluster_id not in deg_results['names'].dtype.names:
            continue
            
        cluster_mask = full_adata.obs['domain'] == cluster_id
        cluster_data = full_adata[cluster_mask]
        
        if cluster_data.n_obs == 0:
            continue
        
        print(f"  处理原始聚类 {cluster_id} ({cluster_data.n_obs} spots)")
        
        # 获取基因表达数据
        if hasattr(cluster_data.X, 'toarray'):
            X = cluster_data.X.toarray()
        else:
            X = cluster_data.X
        
        # 计算基本统计量
        mean_expr = np.mean(X, axis=0)
        std_expr = np.std(X, axis=0)
        
        # 获取差异表达基因
        deg_genes = deg_results['names'][cluster_id]
        deg_scores = deg_results['scores'][cluster_id]
        deg_pvals = deg_results['pvals'][cluster_id]
        deg_pvals_adj = deg_results['pvals_adj'][cluster_id]
        deg_logfoldchanges = deg_results['logfoldchanges'][cluster_id]
        
        # 过滤显著的差异表达基因 - 使用与原始聚类相同的放宽标准
        significant_deg_genes = []
        deg_gene_info = {}
        
        # 🔧 使用相同的逐步放宽标准
        criteria_sets = [
            {'pval_adj': 0.05, 'logfc': 0.5, 'name': 'strict'},
            {'pval_adj': 0.1, 'logfc': 0.25, 'name': 'moderate'},  
            {'pval_adj': 0.2, 'logfc': 0.1, 'name': 'relaxed'},
            {'pval_adj': 1.0, 'logfc': 0.0, 'name': 'top_50'}  # 取前50个
        ]
        
        for criteria in criteria_sets:
            significant_deg_genes = []
            deg_gene_info = {}
            
            for j, (gene, score, pval, pval_adj, logfc) in enumerate(zip(
                deg_genes, deg_scores, deg_pvals, deg_pvals_adj, deg_logfoldchanges
            )):
                # 使用当前标准筛选
                if (pd.notna(gene) and 
                    pval_adj < criteria['pval_adj'] and 
                    logfc > criteria['logfc']):
                    
                    significant_deg_genes.append(gene)
                    deg_gene_info[gene] = {
                        'score': score,
                        'pval': pval,
                        'pval_adj': pval_adj,
                        'logfoldchange': logfc,
                        'rank': j
                    }
            
            # 如果是top_50标准，直接取前50个
            if criteria['name'] == 'top_50' and len(significant_deg_genes) < 10:
                significant_deg_genes = []
                deg_gene_info = {}
                for j, gene in enumerate(deg_genes[:50]):
                    if pd.notna(gene):
                        significant_deg_genes.append(gene)
                        deg_gene_info[gene] = {
                            'score': deg_scores[j] if j < len(deg_scores) else 0,
                            'pval': deg_pvals[j] if j < len(deg_pvals) else 1,
                            'pval_adj': deg_pvals_adj[j] if j < len(deg_pvals_adj) else 1,
                            'logfoldchange': deg_logfoldchanges[j] if j < len(deg_logfoldchanges) else 0,
                            'rank': j
                        }
            
            print(f"    原始聚类{cluster_id}使用{criteria['name']}标准: 找到 {len(significant_deg_genes)} 个DEG基因")
            
            # 如果找到足够的基因，就停止
            if len(significant_deg_genes) >= 10:
                break
        
        original_features[cluster_id] = {
            'mean_expression': mean_expr,
            'std_expression': std_expr,
            'deg_genes': significant_deg_genes,  # 差异表达基因列表
            'deg_gene_info': deg_gene_info,      # 详细的差异表达信息
            'n_spots': cluster_data.n_obs,
            'n_deg_genes': len(significant_deg_genes),
            'method': 'differential_expression'
        }
        
        print(f"    差异表达基因数量: {len(significant_deg_genes)}")
    
    return original_features

# def _compute_new_cluster_features_with_deg(region_adata, cluster_results):
#     """为新聚类计算差异表达基因特征"""
    
#     print("为新聚类计算差异表达基因特征...")
    
#     # 创建临时的聚类标签
#     hard_labels = cluster_results['hard_labels']
#     n_components = cluster_results['n_components']
    
#     # 为region_adata添加临时的聚类标签
#     region_adata.obs['temp_recluster'] = hard_labels.astype(str)
    
#     # 运行差异表达分析
#     try:
#         sc.tl.rank_genes_groups(
#             region_adata,
#             groupby='temp_recluster',
#             method='wilcoxon',
#             key_added='recluster_deg',
#             n_genes=200,
#             use_raw=False
#         )
#         print("  新聚类差异表达分析完成")
#     except Exception as e:
#         print(f"  新聚类差异表达分析失败: {e}")
#         # 回退到原来的方法
#         return _compute_new_cluster_features_fallback(region_adata, cluster_results)
    
#     # 提取每个新聚类的特征
#     new_cluster_features = {}
#     deg_results = region_adata.uns['recluster_deg']
    
#     for i in range(n_components):
#         cluster_mask = hard_labels == i
#         cluster_data = region_adata[cluster_mask]
        
#         if cluster_data.n_obs == 0:
#             new_cluster_features[i] = {'method': 'empty', 'signature': None}
#             continue
            
#         cluster_id_str = str(i)
#         if cluster_id_str not in deg_results['names'].dtype.names:
#             # 回退方法
#             new_cluster_features[i] = _compute_single_cluster_features_fallback(region_adata, cluster_mask)
#             continue
            
#         print(f"  处理新聚类 {i} ({cluster_data.n_obs} spots)")
        
#         # 获取基因表达数据
#         if hasattr(cluster_data.X, 'toarray'):
#             X = cluster_data.X.toarray()
#         else:
#             X = cluster_data.X
        
#         mean_expr = np.mean(X, axis=0)
#         std_expr = np.std(X, axis=0)
        
#         # 获取差异表达基因
#         deg_genes = deg_results['names'][cluster_id_str]
#         deg_scores = deg_results['scores'][cluster_id_str]
#         deg_pvals = deg_results['pvals'][cluster_id_str]
#         deg_pvals_adj = deg_results['pvals_adj'][cluster_id_str]
#         deg_logfoldchanges = deg_results['logfoldchanges'][cluster_id_str]
        
#         # 筛选显著的差异表达基因 - 🔧 使用与原始聚类相同的逐步放宽标准
#         significant_deg_genes = []
#         deg_gene_info = {}
        
#         # 使用相同的逐步放宽标准
#         criteria_sets = [
#             {'pval_adj': 0.05, 'logfc': 0.5, 'name': 'strict'},
#             {'pval_adj': 0.1, 'logfc': 0.25, 'name': 'moderate'},  
#             {'pval_adj': 0.2, 'logfc': 0.1, 'name': 'relaxed'},
#             {'pval_adj': 1.0, 'logfc': 0.0, 'name': 'top_50'}  # 取前50个
#         ]
        
#         for criteria in criteria_sets:
#             significant_deg_genes = []
#             deg_gene_info = {}
            
#             for j, (gene, score, pval, pval_adj, logfc) in enumerate(zip(
#                 deg_genes, deg_scores, deg_pvals, deg_pvals_adj, deg_logfoldchanges
#             )):
#                 # 使用当前标准筛选
#                 if (pd.notna(gene) and 
#                     pval_adj < criteria['pval_adj'] and 
#                     logfc > criteria['logfc']):
                    
#                     significant_deg_genes.append(gene)
#                     deg_gene_info[gene] = {
#                         'score': score,
#                         'pval': pval,
#                         'pval_adj': pval_adj,
#                         'logfoldchange': logfc,
#                         'rank': j
#                     }
            
#             # 如果是top_50标准，直接取前50个
#             if criteria['name'] == 'top_50' and len(significant_deg_genes) < 10:
#                 significant_deg_genes = []
#                 deg_gene_info = {}
#                 for j, gene in enumerate(deg_genes[:50]):
#                     if pd.notna(gene):
#                         significant_deg_genes.append(gene)
#                         deg_gene_info[gene] = {
#                             'score': deg_scores[j] if j < len(deg_scores) else 0,
#                             'pval': deg_pvals[j] if j < len(deg_pvals) else 1,
#                             'pval_adj': deg_pvals_adj[j] if j < len(deg_pvals_adj) else 1,
#                             'logfoldchange': deg_logfoldchanges[j] if j < len(deg_logfoldchanges) else 0,
#                             'rank': j
#                         }
            
#             print(f"    新聚类{i}使用{criteria['name']}标准: 找到 {len(significant_deg_genes)} 个DEG基因")
            
#             # 如果找到足够的基因，就停止
#             if len(significant_deg_genes) >= 10:
#                 break
        
#         new_cluster_features[i] = {
#             'method': 'differential_expression',
#             'mean_expression': mean_expr,
#             'std_expression': std_expr,
#             'deg_genes': significant_deg_genes,
#             'deg_gene_info': deg_gene_info,
#             'n_spots': cluster_data.n_obs,
#             'n_deg_genes': len(significant_deg_genes)
#         }
        
#         print(f"    差异表达基因数量: {len(significant_deg_genes)}")
    
#     # 清理临时标签
#     del region_adata.obs['temp_recluster']
    
#     return new_cluster_features

# def _map_by_deg_similarity(region_adata, cluster_mask, new_cluster_features, 
#                           original_cluster_features, all_original_clusters):
#     """基于差异表达基因相似性进行映射"""
    
#     cluster_data = region_adata[cluster_mask]
    
#     if cluster_data.n_obs == 0 or new_cluster_features['method'] == 'empty':
#         return {
#             'predicted_type': 'empty_cluster',
#             'confidence': 0.0,
#             'mapping_source': 'none',
#             'relationship': 'empty',
#             'similarity_scores': {}
#         }
    
#     print(f"  基于差异表达基因计算相似性...")
    
#     # 获取新聚类的特征
#     new_mean_expr = new_cluster_features['mean_expression']
#     new_deg_genes = set(new_cluster_features['deg_genes'])
    
#     # 🔍 调试信息：检查新聚类的DEG基因
#     print(f"    新聚类DEG基因数量: {len(new_deg_genes)}")
#     if len(new_deg_genes) > 0:
#         print(f"    新聚类DEG基因列表: {list(new_deg_genes)}")
#     else:
#         print(f"    ⚠️ 新聚类没有DEG基因！")
    
#     similarity_scores = {}
    
#     # 计算与每个原始聚类的相似性
#     for orig_cluster_id in all_original_clusters:
#         if orig_cluster_id not in original_cluster_features:
#             continue
        
#         orig_features = original_cluster_features[orig_cluster_id]
#         orig_mean_expr = orig_features['mean_expression']
#         orig_deg_genes = set(orig_features['deg_genes'])
        
#         # 🔍 调试信息：检查原始聚类的DEG基因
#         if orig_cluster_id == list(all_original_clusters)[0]:  # 只对第一个聚类输出详细信息
#             print(f"    原始聚类 {orig_cluster_id} DEG基因数量: {len(orig_deg_genes)}")
#             if len(orig_deg_genes) > 0:
#                 print(f"    原始聚类 {orig_cluster_id} DEG基因示例: {list(orig_deg_genes)[:5]}")
#             else:
#                 print(f"    ⚠️ 原始聚类 {orig_cluster_id} 没有DEG基因！")
        
#         # 1. 基因表达相关性（全基因组）
#         correlation = np.corrcoef(new_mean_expr, orig_mean_expr)[0, 1]
#         if np.isnan(correlation):
#             correlation = 0.0
        
#         # 2. 差异表达基因重叠（Jaccard相似性）
#         common_deg_genes = new_deg_genes.intersection(orig_deg_genes)
#         union_deg_genes = new_deg_genes.union(orig_deg_genes)
        
#         # 🔍 调试信息：检查基因重叠情况
#         if orig_cluster_id == list(all_original_clusters)[0]:  # 只对第一个聚类输出详细信息
#             print(f"    共同DEG基因数量: {len(common_deg_genes)}")
#             print(f"    并集DEG基因数量: {len(union_deg_genes)}")
#             if len(common_deg_genes) > 0:
#                 print(f"    共同DEG基因示例: {list(common_deg_genes)[:3]}")
            
#             # 检查基因名称格式差异
#             if len(new_deg_genes) > 0 and len(orig_deg_genes) > 0:
#                 new_sample = list(new_deg_genes)[0]
#                 orig_sample = list(orig_deg_genes)[0]
#                 print(f"    新聚类基因名格式示例: '{new_sample}' (类型: {type(new_sample)})")
#                 print(f"    原始聚类基因名格式示例: '{orig_sample}' (类型: {type(orig_sample)})")
        
#         # 打印与原始聚类的重叠DEG基因
#         if len(common_deg_genes) > 0:
#             print(f"    与原始聚类 {orig_cluster_id} 重叠的DEG基因: {list(common_deg_genes)}")
        
#         jaccard_deg = len(common_deg_genes) / len(union_deg_genes) if len(union_deg_genes) > 0 else 0
        
#         # 3. 差异表达基因的表达相关性
#         if len(common_deg_genes) > 0:
#             # 获取共同差异基因的表达值
#             common_gene_indices = [i for i, gene in enumerate(region_adata.var.index) if gene in common_deg_genes]
#             if len(common_gene_indices) > 1:
#                 new_common_expr = new_mean_expr[common_gene_indices]
#                 orig_common_expr = orig_mean_expr[common_gene_indices]
#                 deg_correlation = np.corrcoef(new_common_expr, orig_common_expr)[0, 1]
#                 if np.isnan(deg_correlation):
#                     deg_correlation = 0.0
#             else:
#                 deg_correlation = 0.0
#         else:
#             deg_correlation = 0.0
        
#         # 4. 余弦相似性（全基因组）
#         cosine_sim = cosine_similarity([new_mean_expr], [orig_mean_expr])[0, 0]
#         if np.isnan(cosine_sim):
#             cosine_sim = 0.0
        
#         # 综合相似性分数（更重视差异表达基因的重叠）
#         combined_similarity = (
#             correlation * 0.25 +           # 全基因组相关性
#             jaccard_deg * 0.4 +           # 差异基因重叠（最重要）
#             deg_correlation * 0.25 +      # 共同差异基因表达相关性
#             cosine_sim * 0.1              # 余弦相似性
#         )
        
#         similarity_scores[orig_cluster_id] = {
#             'correlation': correlation,
#             'jaccard_deg_similarity': jaccard_deg,
#             'deg_correlation': deg_correlation,
#             'cosine_similarity': cosine_sim,
#             'combined_similarity': combined_similarity,
#             'common_deg_genes': list(common_deg_genes),
#             'n_common_deg_genes': len(common_deg_genes),
#             'new_deg_genes_count': len(new_deg_genes),
#             'orig_deg_genes_count': len(orig_deg_genes)
#         }
        
#         print(f"    与原始聚类 {orig_cluster_id}: 全基因相关性={correlation:.3f}, DEG重叠={jaccard_deg:.3f}, DEG相关性={deg_correlation:.3f}, 综合={combined_similarity:.3f}")
    
#     # 找到最相似的原始聚类
#     if similarity_scores:
#         best_match = max(similarity_scores.keys(), key=lambda x: similarity_scores[x]['combined_similarity'])
#         best_similarity = similarity_scores[best_match]['combined_similarity']
        
#         # 根据相似性分数决定映射策略（调整阈值，因为差异基因重叠通常较低）
#         if best_similarity >= 0.6:
#             predicted_type = str(best_match)
#             relationship = 'high_deg_similarity_match'
#             mapping_source = 'differential_expression_similarity'
#             confidence = best_similarity
#         elif best_similarity >= 0.4:
#             predicted_type = str(best_match)
#             relationship = 'moderate_deg_similarity_match'
#             mapping_source = 'differential_expression_similarity'
#             confidence = best_similarity
#         elif best_similarity >= 0.2:
#             predicted_type = f"deg_similar_to_{best_match}"
#             relationship = 'low_deg_similarity_subtype'
#             mapping_source = 'differential_expression_similarity'
#             confidence = best_similarity
#         else:
#             predicted_type = "novel_deg_cluster"
#             relationship = 'novel_deg_discovery'
#             mapping_source = 'no_deg_similarity'
#             confidence = 0.1
#     else:
#         predicted_type = "unknown_deg_cluster"
#         relationship = 'unknown'
#         mapping_source = 'no_deg_comparison'
#         confidence = 0.0
#         best_match = None
    
#     # 同时检查区域内的原始标签分布作为参考
#     original_labels_in_cluster = cluster_data.obs['domain'].value_counts()
#     most_common_original = original_labels_in_cluster.index[0] if len(original_labels_in_cluster) > 0 else None
#     consistency = original_labels_in_cluster.iloc[0] / cluster_data.n_obs if len(original_labels_in_cluster) > 0 else 0
    
#     mapping_result = {
#         'predicted_type': predicted_type,
#         'confidence': confidence,
#         'mapping_source': mapping_source,
#         'relationship': relationship,
#         'best_similarity_match': best_match,
#         'best_similarity_score': best_similarity if similarity_scores else 0.0,
#         'similarity_scores': similarity_scores,
#         'original_distribution': dict(original_labels_in_cluster),
#         'most_common_original': most_common_original,
#         'original_consistency': consistency,
#         'size': cluster_data.n_obs
#     }
    
#     print(f"  映射结果: {predicted_type} (置信度: {confidence:.3f}, 关系: {relationship})")
    
#     return mapping_result



def _feature_functional_mapping(region_adata, cluster_results, full_adata):
    """基于基因表达特征相似性的映射"""
    
    print("开始基于基因表达特征相似性的映射...")
    
    hard_labels = cluster_results['hard_labels']
    q_matrix = cluster_results['q_matrix']
    n_components = cluster_results['n_components']
    
    mapping_results = {}
    feature_signatures = {}
    
    # 获取全数据集的原始聚类信息
    all_original_clusters = full_adata.obs['domain'].unique()
    print(f"全数据集包含的原始聚类: {sorted(all_original_clusters)}")
    
    # 计算全数据集中每个原始聚类的基因表达特征（与原代码相同）
    print("计算原始聚类的基因表达特征...")
    original_cluster_features = _compute_original_cluster_features_with_deg(full_adata, all_original_clusters)
    
    # 计算所有新聚类的特征（修改：增加同源聚类识别）
    print("计算新聚类的基因表达特征...")
    all_new_cluster_features = _compute_new_cluster_features_with_deg(
        region_adata, cluster_results, full_adata, all_original_clusters
    )
    
    for i in range(n_components):
        cluster_mask = hard_labels == i
        cluster_size = np.sum(cluster_mask)
        
        if cluster_size == 0:
            print(f"聚类 {i}: 无数据，跳过")
            continue
        
        print(f"\n处理聚类 {i} 的 {cluster_size} spots")
        
        # 获取当前聚类的特征
        if i in all_new_cluster_features:
            new_cluster_features = all_new_cluster_features[i]
        else:
            new_cluster_features = {'method': 'empty', 'signature': None}
        
        feature_signatures[i] = new_cluster_features
        
        # 基于基因表达特征相似性进行映射（修改：增加同源聚类验证）
        mapping_result = _map_by_deg_similarity(
            region_adata, cluster_mask, new_cluster_features, 
            original_cluster_features, all_original_clusters
        )
        mapping_results[i] = mapping_result
    
    print("基于基因表达特征相似性的映射完成")
    return mapping_results, feature_signatures

def _compute_new_cluster_features_with_deg(region_adata, cluster_results, full_adata, all_original_clusters):
    """为新聚类计算差异表达基因特征（改进版：分层次对比）"""
    
    print("为新聚类计算差异表达基因特征...")
    
    # 创建临时的聚类标签
    hard_labels = cluster_results['hard_labels']
    n_components = cluster_results['n_components']
    
    # 为region_adata添加临时的聚类标签
    region_adata.obs['temp_recluster'] = hard_labels.astype(str)
    
    # 结果字典
    new_cluster_features = {}
    
    # 第一步：初步匹配新聚类的同源原始聚类
    preliminary_matches = _identify_homologous_original_clusters(
        region_adata, cluster_results, full_adata, all_original_clusters
    )
    
    for i in range(n_components):
        cluster_mask = hard_labels == i
        cluster_data = region_adata[cluster_mask]
        
        if cluster_data.n_obs == 0:
            new_cluster_features[i] = {'method': 'empty', 'signature': None}
            continue
            
        print(f"  处理新聚类 {i} ({cluster_data.n_obs} spots)")
        
        # 获取初步匹配的同源原始聚类
        homologous_clusters = preliminary_matches.get(i, [])
        
        # 第二步：分阶段计算差异表达基因
        # 阶段1：与除同源聚类外的其他原始聚类对比（排除同源干扰）
        stage1_deg = _compute_deg_against_non_homologous(
            region_adata, i, full_adata, homologous_clusters, all_original_clusters
        )
        
        # 阶段2：如果有同源聚类，与同源聚类单独对比（聚焦亚群差异）
        if homologous_clusters:
            stage2_deg = _compute_deg_against_homologous(
                region_adata, i, full_adata, homologous_clusters
            )
        else:
            stage2_deg = {'deg_genes': [], 'deg_gene_info': {}}
        
        # 合并两个阶段的结果：保留与非同源的差异，补充与同源的独特差异
        merged_deg_genes = set(stage1_deg['deg_genes'])
        # 只添加在stage2中但不在stage1中的基因（避免稀释核心差异）
        for gene in stage2_deg['deg_genes']:
            if gene not in merged_deg_genes:
                 merged_deg_genes.add(gene)
        
        # 获取基因表达数据
        if hasattr(cluster_data.X, 'toarray'):
            X = cluster_data.X.toarray()
        else:
            X = cluster_data.X
        
        mean_expr = np.mean(X, axis=0)
        std_expr = np.std(X, axis=0)
        
        # 构建最终特征
        new_cluster_features[i] = {
            'method': 'differential_expression',
            'mean_expression': mean_expr,
            'std_expression': std_expr,
            'deg_genes': list(merged_deg_genes),
            'deg_gene_info': {**stage1_deg['deg_gene_info'], **stage2_deg['deg_gene_info']},
            'n_spots': cluster_data.n_obs,
            'n_deg_genes': len(merged_deg_genes),
            'homologous_original_clusters': homologous_clusters,
            'stage1_deg_count': len(stage1_deg['deg_genes']),
            'stage2_deg_count': len(stage2_deg['deg_genes'])
        }
        
        print(f"    合并后差异表达基因数量: {len(merged_deg_genes)}")
        print(f"    同源原始聚类: {homologous_clusters}")
    
    # 清理临时标签
    del region_adata.obs['temp_recluster']
    
    return new_cluster_features

def _identify_homologous_original_clusters(region_adata, cluster_results, full_adata, all_original_clusters):
    """识别每个新聚类的同源原始聚类（初步匹配）"""
    
    print("识别新聚类的同源原始聚类...")
    
    hard_labels = cluster_results['hard_labels']
    n_components = cluster_results['n_components']
    
    # 计算全数据集的基因表达均值（用于快速相似度计算）
    full_mean_expr = {}
    for cluster_id in all_original_clusters:
        cluster_mask = full_adata.obs['domain'] == cluster_id
        cluster_data = full_adata[cluster_mask]
        if hasattr(cluster_data.X, 'toarray'):
            X = cluster_data.X.toarray()
        else:
            X = cluster_data.X
        full_mean_expr[cluster_id] = np.mean(X, axis=0)
    
    # 结果字典：新聚类ID -> 同源原始聚类列表
    homologous_matches = {}
    
    for i in range(n_components):
        cluster_mask = hard_labels == i
        cluster_data = region_adata[cluster_mask]
        
        if cluster_data.n_obs == 0:
            continue
            
        # 计算当前新聚类的基因表达均值
        if hasattr(cluster_data.X, 'toarray'):
            X = cluster_data.X.toarray()
        else:
            X = cluster_data.X
        new_mean_expr = np.mean(X, axis=0)
        
        # 计算与每个原始聚类的相似度（使用全基因表达相关性）
        similarities = {}
        for cluster_id in all_original_clusters:
            corr = np.corrcoef(new_mean_expr, full_mean_expr[cluster_id])[0, 1]
            similarities[cluster_id] = corr
        
        # 排序并选择最相似的前3个原始聚类（阈值：相关性>0.7）
        sorted_clusters = sorted(similarities.items(), key=lambda x: x[1], reverse=True)
        top_clusters = [c for c, sim in sorted_clusters if sim > 0.7][:3]

        if top_clusters:
            homologous_matches[i] = top_clusters
            print(f"  新聚类 {i} 的同源原始聚类: {top_clusters}")
        else:
            homologous_matches[i] = []
            print(f"  新聚类 {i}: 未找到显著同源的原始聚类")
    
    return homologous_matches

def _compute_deg_against_non_homologous(region_adata, cluster_idx, full_adata, homologous_clusters, all_original_clusters):
    """计算新聚类与非同源原始聚类的差异表达基因"""
    
    # 创建临时的参考组：排除同源聚类的所有原始数据
    non_homologous_mask = pd.Series([True] * len(full_adata), index=full_adata.obs_names)
    for cluster_id in homologous_clusters:
        non_homologous_mask &= (full_adata.obs['domain'] != cluster_id)
    
    non_homologous_data = full_adata[non_homologous_mask]
    
    # 获取新聚类的子集
    cluster_mask = region_adata.obs['temp_recluster'] == str(cluster_idx)
    cluster_data = region_adata[cluster_mask]
    
    # 转为 dense
    cluster_X = cluster_data.X
    non_homologous_X = non_homologous_data.X
    if hasattr(cluster_X, 'toarray'):
        cluster_X = cluster_X.toarray()
    if hasattr(non_homologous_X, 'toarray'):
        non_homologous_X = non_homologous_X.toarray()
    
    # 拼接表达矩阵和 obs
    X = np.vstack([cluster_X, non_homologous_X])
    obs = pd.concat([cluster_data.obs, non_homologous_data.obs], axis=0)
    obs = obs.copy()  # 避免潜在的 SettingWithCopyWarning
    
    print(f"[DEBUG] cluster_X shape: {cluster_X.shape}, non_homologous_X shape: {non_homologous_X.shape}")
    print(f"[DEBUG] cluster_data.obs shape: {cluster_data.obs.shape}, non_homologous_data.obs shape: {non_homologous_data.obs.shape}")
    print(f"[DEBUG] X shape: {X.shape}, obs shape: {obs.shape}")
    assert X.shape[0] == obs.shape[0], f"X和obs行数不一致: {X.shape[0]} vs {obs.shape[0]}"
    
    # 创建临时AnnData用于差异分析
    temp_adata = sc.AnnData(
        X=X,
        obs=obs,
        var=region_adata.var
    )
    
    # 添加临时标签：新聚类 vs 非同源原始聚类
    temp_adata.obs['deg_group'] = ['new_cluster'] * cluster_data.n_obs + ['non_homologous'] * non_homologous_data.n_obs
    
    # 运行差异表达分析
    try:
        sc.tl.rank_genes_groups(
            temp_adata,
            groupby='deg_group',
            groups=['new_cluster'],
            reference='non_homologous',
            method='wilcoxon',
            key_added='deg_vs_non_homologous',
            n_genes=200,
            use_raw=False
        )
    except Exception as e:
        print(f"  计算与非同源聚类的DEG失败: {e}")
        return {'deg_genes': [], 'deg_gene_info': {}}
    
    # 提取差异表达基因
    deg_results = temp_adata.uns['deg_vs_non_homologous']
    deg_genes = deg_results['names']['new_cluster']
    deg_pvals_adj = deg_results['pvals_adj']['new_cluster']
    deg_logfoldchanges = deg_results['logfoldchanges']['new_cluster']
    
    # 筛选显著的差异表达基因
    significant_deg_genes = []
    deg_gene_info = {}
    
    for j, (gene, pval_adj, logfc) in enumerate(zip(deg_genes, deg_pvals_adj, deg_logfoldchanges)):
        if pd.notna(gene) and pval_adj < 0.05 and logfc > 0.5:
            significant_deg_genes.append(gene)
            deg_gene_info[gene] = {
                'pval_adj': pval_adj,
                'logfoldchange': logfc,
                'rank': j,
                'comparison': 'new_vs_non_homologous'
            }
    
    return {
        'deg_genes': significant_deg_genes,
        'deg_gene_info': deg_gene_info
    }

def _compute_deg_against_homologous(region_adata, cluster_idx, full_adata, homologous_clusters):
    """计算新聚类与同源原始聚类的差异表达基因"""
    
    # 合并所有同源原始聚类的数据
    # homologous_data = None
    homologous_data_list = []
    for cluster_id in homologous_clusters:
        cluster_data_hom = full_adata[full_adata.obs['domain'] == cluster_id]
        # 去除与新聚类重叠的 obs_names
        cluster_data_hom = cluster_data_hom[~cluster_data_hom.obs_names.isin(region_adata.obs[region_adata.obs['temp_recluster'] == str(cluster_idx)].index)]
        # if homologous_data is None:
        #     homologous_data = cluster_data_hom.copy()
        # else:
        #     homologous_data = homologous_data.concatenate(cluster_data_hom)
        if len(cluster_data_hom) > 0:
            homologous_data_list.append(cluster_data_hom)
    
    # 使用 concatenate 合并所有数据
    if len(homologous_data_list) == 0:
        print(f"⚠️ 警告：没有找到同源聚类数据，跳过DEG分析")
        return {'deg_genes': [], 'deg_gene_info': []}
    elif len(homologous_data_list) == 1:
        homologous_data = homologous_data_list[0].copy()
    else:
        homologous_data = homologous_data_list[0].concatenate(*homologous_data_list[1:])

    # 确保索引唯一
    if not homologous_data.obs_names.is_unique:
        homologous_data.obs_names_make_unique()
    cluster_data = region_adata[region_adata.obs['temp_recluster'] == str(cluster_idx)]
    if not cluster_data.obs_names.is_unique:
        cluster_data.obs_names_make_unique()

    # 检查是否有数据
    if len(cluster_data) == 0:
        print(f"⚠️ 警告：cluster {cluster_idx} 没有数据，跳过DEG分析")
        return {'deg_genes': [], 'deg_gene_info': []}
    
    if len(homologous_data) == 0:
        print(f"⚠️ 警告：同源聚类没有数据，跳过DEG分析")
        return {'deg_genes': [], 'deg_gene_info': []}

    # 创建临时AnnData用于差异分析
    # temp_adata = sc.AnnData(
    #     X=np.vstack([
    #         cluster_data.X,
    #         homologous_data.X
    #     ]),
    #     obs=pd.concat([
    #         cluster_data.obs,
    #         homologous_data.obs
    #     ], axis=0),
    # 使用 concatenate 而不是手动 vstack 和 concat，更安全
    from scipy import sparse
    
    # 如果是稀疏矩阵，使用 sparse.vstack，否则用 np.vstack
    if sparse.issparse(cluster_data.X):
        combined_X = sparse.vstack([cluster_data.X, homologous_data.X])
    else:
        combined_X = np.vstack([cluster_data.X, homologous_data.X])
    
    print(f"combined_X 形状: {combined_X.shape}")
    
    combined_obs = pd.concat([
        cluster_data.obs,
        homologous_data.obs
    ], axis=0)
    
    print(f"combined_obs 形状: {combined_obs.shape}")
    print(f"region_adata.var 形状: {region_adata.var.shape}")
    
    temp_adata = sc.AnnData(
        X=combined_X,
        obs=combined_obs,
        var=region_adata.var
    )
    
    # 添加临时标签：新聚类 vs 同源原始聚类
    temp_adata.obs['deg_group'] = ['new_cluster'] * len(cluster_data) + ['homologous'] * len(homologous_data)
    
    # 运行差异表达分析
    try:
        sc.tl.rank_genes_groups(
            temp_adata,
            groupby='deg_group',
            groups=['new_cluster'],
            reference='homologous',
            method='wilcoxon',
            key_added='deg_vs_homologous',
            n_genes=200,
            use_raw=False
        )
    except Exception as e:
        print(f"  计算与同源聚类的DEG失败: {e}")
        return {'deg_genes': [], 'deg_gene_info': {}}
    
    # 提取差异表达基因
    deg_results = temp_adata.uns['deg_vs_homologous']
    deg_genes = deg_results['names']['new_cluster']
    deg_pvals_adj = deg_results['pvals_adj']['new_cluster']
    deg_logfoldchanges = deg_results['logfoldchanges']['new_cluster']
    
    # 筛选显著的差异表达基因（使用更严格的阈值，因为同源聚类差异较小）
    significant_deg_genes = []
    deg_gene_info = {}
    
    for j, (gene, pval_adj, logfc) in enumerate(zip(deg_genes, deg_pvals_adj, deg_logfoldchanges)):
        if pd.notna(gene) and pval_adj < 0.01 and logfc > 0.7:  # 更严格的阈值
            significant_deg_genes.append(gene)
            deg_gene_info[gene] = {
                'pval_adj': pval_adj,
                'logfoldchange': logfc,
                'rank': j,
                'comparison': 'new_vs_homologous'
            }
    
    return {
        'deg_genes': significant_deg_genes,
        'deg_gene_info': deg_gene_info
    }

def _map_by_deg_similarity(region_adata, cluster_mask, new_cluster_features, 
                          original_cluster_features, all_original_clusters):
    """基于差异表达基因相似性进行映射（改进版：考虑同源聚类验证）"""
    
    cluster_data = region_adata[cluster_mask]
    
    if cluster_data.n_obs == 0 or new_cluster_features['method'] == 'empty':
        return {
            'predicted_type': 'empty_cluster',
            'confidence': 0.0,
            'mapping_source': 'none',
            'relationship': 'empty',
            'similarity_scores': {}
        }
    
    print(f"  基于差异表达基因计算相似性...")
    
    # 获取新聚类的特征
    new_mean_expr = new_cluster_features['mean_expression']
    new_deg_genes = set(new_cluster_features['deg_genes'])
    homologous_clusters = new_cluster_features.get('homologous_original_clusters', [])
    
    # 计算与每个原始聚类的相似性
    similarity_scores = {}
    
    for orig_cluster_id in all_original_clusters:
        if orig_cluster_id not in original_cluster_features:
            continue
        
        orig_features = original_cluster_features[orig_cluster_id]
        orig_mean_expr = orig_features['mean_expression']
        orig_deg_genes = set(orig_features['deg_genes'])
        
        # 1. 基因表达相关性（全基因组）
        correlation = np.corrcoef(new_mean_expr, orig_mean_expr)[0, 1]
        if np.isnan(correlation):
            correlation = 0.0
        
        # 2. 差异表达基因重叠（Jaccard相似性）
        common_deg_genes = new_deg_genes.intersection(orig_deg_genes)
        union_deg_genes = new_deg_genes.union(orig_deg_genes)
        jaccard_deg = len(common_deg_genes) / len(union_deg_genes) if len(union_deg_genes) > 0 else 0
        
        # 3. 差异表达基因的表达相关性
        if len(common_deg_genes) > 0:
            common_gene_indices = [i for i, gene in enumerate(region_adata.var.index) if gene in common_deg_genes]
            if len(common_gene_indices) > 1:
                new_common_expr = new_mean_expr[common_gene_indices]
                orig_common_expr = orig_mean_expr[common_gene_indices]
                deg_correlation = np.corrcoef(new_common_expr, orig_common_expr)[0, 1]
                if np.isnan(deg_correlation):
                    deg_correlation = 0.0
            else:
                deg_correlation = 0.0
        else:
            deg_correlation = 0.0
        
        # 4. 余弦相似性（全基因组）
        cosine_sim = cosine_similarity([new_mean_expr], [orig_mean_expr])[0, 0]
        if np.isnan(cosine_sim):
            cosine_sim = 0.0
        
        # 5. 同源聚类增强因子（如果orig_cluster_id是同源聚类之一，增加权重）
        homology_factor = 1.2 if orig_cluster_id in homologous_clusters else 1.0
        
        # 综合相似性分数（更重视差异表达基因的重叠，加入同源增强因子）
        combined_similarity = (
            correlation * 0.25 +           # 全基因组相关性
            jaccard_deg * 0.4 * homology_factor +  # 差异基因重叠（最重要，同源聚类增强）
            deg_correlation * 0.25 +      # 共同差异基因表达相关性
            cosine_sim * 0.1              # 余弦相似性
        )
        
        similarity_scores[orig_cluster_id] = {
            'correlation': correlation,
            'jaccard_deg_similarity': jaccard_deg,
            'deg_correlation': deg_correlation,
            'cosine_similarity': cosine_sim,
            'combined_similarity': combined_similarity,
            'common_deg_genes': list(common_deg_genes),
            'n_common_deg_genes': len(common_deg_genes),
            'new_deg_genes_count': len(new_deg_genes),
            'orig_deg_genes_count': len(orig_deg_genes),
            'is_homologous': orig_cluster_id in homologous_clusters
        }
        
        print(f"    与原始聚类 {orig_cluster_id}: 全基因相关性={correlation:.3f}, DEG重叠={jaccard_deg:.3f}, DEG相关性={deg_correlation:.3f}, 综合={combined_similarity:.3f}, 同源={orig_cluster_id in homologous_clusters}")
    
    # 找到最相似的原始聚类
    if similarity_scores:
        best_match = max(similarity_scores.keys(), key=lambda x: similarity_scores[x]['combined_similarity'])
        best_similarity = similarity_scores[best_match]['combined_similarity']
        
        # 基于同源聚类验证，调整映射策略
        if best_match in homologous_clusters:
            # 最佳匹配是同源聚类之一，提高置信度阈值
            if best_similarity >= 0.5:
                predicted_type = str(best_match)
                relationship = 'high_deg_similarity_match'
                mapping_source = 'differential_expression_similarity'
                confidence = best_similarity
            elif best_similarity >= 0.3:
                predicted_type = str(best_match)
                relationship = 'moderate_deg_similarity_match'
                mapping_source = 'differential_expression_similarity'
                confidence = best_similarity
            else:
                predicted_type = f"deg_similar_to_{best_match}"
                relationship = 'low_deg_similarity_subtype'
                mapping_source = 'differential_expression_similarity'
                confidence = best_similarity
        else:
            # 最佳匹配不是同源聚类，降低置信度
            if best_similarity >= 0.6:
                predicted_type = str(best_match)
                relationship = 'high_deg_similarity_match'
                mapping_source = 'differential_expression_similarity'
                confidence = best_similarity * 0.8  # 降低置信度
            elif best_similarity >= 0.4:
                predicted_type = str(best_match)
                relationship = 'moderate_deg_similarity_match'
                mapping_source = 'differential_expression_similarity'
                confidence = best_similarity * 0.8
            else:
                predicted_type = "novel_deg_cluster"
                relationship = 'novel_deg_discovery'
                mapping_source = 'no_deg_similarity'
                confidence = 0.1
    else:
        predicted_type = "unknown_deg_cluster"
        relationship = 'unknown'
        mapping_source = 'no_deg_comparison'
        confidence = 0.0
        best_match = None
    
    # 同时检查区域内的原始标签分布作为参考
    original_labels_in_cluster = cluster_data.obs['domain'].value_counts()
    most_common_original = original_labels_in_cluster.index[0] if len(original_labels_in_cluster) > 0 else None
    consistency = original_labels_in_cluster.iloc[0] / cluster_data.n_obs if len(original_labels_in_cluster) > 0 else 0
    
    mapping_result = {
        'predicted_type': predicted_type,
        'confidence': confidence,
        'mapping_source': mapping_source,
        'relationship': relationship,
        'best_similarity_match': best_match,
        'best_similarity_score': best_similarity if similarity_scores else 0.0,
        'similarity_scores': similarity_scores,
        'original_distribution': dict(original_labels_in_cluster),
        'most_common_original': most_common_original,
        'original_consistency': consistency,
        'size': cluster_data.n_obs,
        'homologous_original_clusters': homologous_clusters
    }
    
    print(f"  映射结果: {predicted_type} (置信度: {confidence:.3f}, 关系: {relationship})")
    
    return mapping_result



def _biological_validation(region_adata, cluster_results):
    """简化的生物学验证"""
    
    print("开始生物学验证...")
    
    hard_labels = cluster_results['hard_labels']
    n_components = cluster_results['n_components']
    
    bio_validation = {}
    
    for i in range(n_components):
        cluster_mask = hard_labels == i
        cluster_size = np.sum(cluster_mask)
        
        if cluster_size == 0:
            bio_validation[i] = {'validation_score': 0.0, 'notes': 'empty_cluster'}
            continue
        
        print(f"验证聚类 {i} 的 {cluster_size} 个spots")
        
        # 简单的验证：检查聚类内的基因表达一致性
        cluster_data = region_adata[cluster_mask]
        
        if hasattr(cluster_data.X, 'toarray'):
            X = cluster_data.X.toarray()
        else:
            X = cluster_data.X
        
        # 计算基因表达的变异系数
        mean_expr = np.mean(X, axis=0)
        std_expr = np.std(X, axis=0)
        
        # 避免除零错误
        cv = np.divide(std_expr, mean_expr, out=np.zeros_like(std_expr), where=mean_expr!=0)
        avg_cv = np.mean(cv[np.isfinite(cv)])
        
        # 验证分数：变异系数越小，聚类内一致性越好
        validation_score = max(0, 1 - avg_cv)
        
        bio_validation[i] = {
            'validation_score': validation_score,
            'avg_cv': avg_cv,
            'cluster_size': cluster_size,
            'notes': 'expression_consistency'
        }
    
    print("生物学验证完成")
    return bio_validation

def _integrate_results_back_improved(
    adata, region_adata, region_mask, cluster_results, mapping_results, bio_validation
):
    """整合结果到完整数据集"""
    
    print("开始整合结果到完整数据集...")
    
    hard_labels = cluster_results['hard_labels']
    q_matrix = cluster_results['q_matrix']
    n_components = cluster_results['n_components']
    
    # 初始化新的列，确保不是Categorical类型
    if 'domain' in adata.obs.columns:
        # 将原始domain转换为字符串类型，避免Categorical问题
        original_domain = adata.obs['domain'].astype(str)
    else:
        original_domain = pd.Series(['unknown'] * adata.n_obs, index=adata.obs.index)
    
    adata.obs['recluster_hard_labels'] = original_domain.copy()  # 保持原始标签
    adata.obs['recluster_result'] = original_domain.copy()  # 映射后的结果
    adata.obs['recluster_confidence'] = 0.0
    adata.obs['recluster_max_prob'] = 0.0
    adata.obs['label_changed'] = False
    adata.obs['recluster_mapping_source'] = 'unknown'
    adata.obs['recluster_relationship'] = 'unknown'
    adata.obs['recluster_mapping_confidence'] = 0.0
    
    # 将重聚类结果整合到选中区域
    region_indices = np.where(region_mask)[0]
    
    for i, region_idx in enumerate(region_indices):
        # 硬标签 - 直接使用整数值
        adata.obs.loc[adata.obs.index[region_idx], 'recluster_hard_labels'] = str(hard_labels[i])
        
        # 置信度
        max_prob = np.max(q_matrix[i, :])
        adata.obs.loc[adata.obs.index[region_idx], 'recluster_confidence'] = max_prob
        adata.obs.loc[adata.obs.index[region_idx], 'recluster_max_prob'] = max_prob
        
        # 检查标签是否改变
        original_label = str(original_domain.iloc[region_idx])
        new_hard_label = hard_labels[i]
        
        # 获取映射结果
        if new_hard_label in mapping_results:
            mapped_result = mapping_results[new_hard_label]
            if isinstance(mapped_result, dict) and 'predicted_type' in mapped_result:
                mapped_label = str(mapped_result['predicted_type'])
                # 保存映射相关信息
                if 'mapping_source' in mapped_result:
                    adata.obs.loc[adata.obs.index[region_idx], 'recluster_mapping_source'] = str(mapped_result['mapping_source'])
                if 'relationship' in mapped_result:
                    adata.obs.loc[adata.obs.index[region_idx], 'recluster_relationship'] = str(mapped_result['relationship'])
                if 'confidence' in mapped_result:
                    adata.obs.loc[adata.obs.index[region_idx], 'recluster_mapping_confidence'] = float(mapped_result['confidence'])
            elif hasattr(mapped_result, 'iloc'):
                # 如果是pandas Series，取第一个值
                mapped_label = str(mapped_result.iloc[0])
            else:
                mapped_label = str(mapped_result)
            
            adata.obs.loc[adata.obs.index[region_idx], 'recluster_result'] = mapped_label
            
            # 判断是否改变（比较映射后的结果和原始标签）
            label_changed = (original_label != mapped_label)
        else:
            # 如果没有映射结果，使用硬标签
            mapped_label = f"recluster_{new_hard_label}"
            adata.obs.loc[adata.obs.index[region_idx], 'recluster_result'] = mapped_label
            adata.obs.loc[adata.obs.index[region_idx], 'recluster_mapping_source'] = 'no_mapping'
            adata.obs.loc[adata.obs.index[region_idx], 'recluster_relationship'] = 'novel'
            label_changed = True  # 新发现的聚类算作改变
        
        adata.obs.loc[adata.obs.index[region_idx], 'label_changed'] = label_changed
    
    # 添加概率信息
    for i in range(n_components):
        prob_col = f'recluster_component_{i}_prob'
        adata.obs[prob_col] = 0.0
        
        for j, region_idx in enumerate(region_indices):
            adata.obs.loc[adata.obs.index[region_idx], prob_col] = q_matrix[j, i]
    
    print(f"整合完成:")
    print(f"  选中区域: {len(region_indices)} spots")
    print(f"  发现聚类数: {n_components}")
    print(f"  标签改变的spots: {adata.obs['label_changed'].sum()}")
    
    return adata

def apply_recluster_analysis(adata, selected_spots_mask, min_cluster_size=5):
    """
    应用重聚类分析的完整流程
    
    Parameters:
    -----------
    adata : AnnData
        你的空间转录组数据，包含adata.obs['domain']初始聚类结果
    selected_spots_mask : array-like
        选中区域的mask，True表示选中的spots
    min_cluster_size : int, default=5
        最小聚类大小，确保生物学意义
    """
    
    # 1. 添加选中区域标记
    adata.obs['selected_region'] = selected_spots_mask
    
    print(f"选中了 {np.sum(selected_spots_mask)} 个spots进行重聚类")
    
    # 2. 运行重聚类分析（使用自动确定的聚类数量范围）
    adata_updated = improved_admixture_reclustering(
        adata,
        region_key='selected_region',
        n_components_range=None,    # 自动根据原始聚类数量确定范围
        use_spatial=True,           # 使用空间信息
        spatial_weight=0.3,         # 空间权重
        confidence_threshold=0.7,   # 置信度阈值
        min_cluster_size=min_cluster_size,  # 传递min_cluster_size参数
        use_hvg=True,              # 使用高变基因
        n_hvg=2000,                # 高变基因数量
        mapping_strategy='feature_functional'  # 映射策略
    )
    
    print("重聚类分析完成")
    return adata_updated

def select_spatial_region(adata, center_idx=None, n_radius=10, expand_radius=5, 
                         min_region_size=20, max_region_size=100):
    """
    选择空间区域进行重聚类分析
    
    Parameters:
    -----------
    adata : AnnData
        空间转录组数据
    center_idx : int, optional
        中心点索引，如果为None则随机选择
    n_radius : int
        中心区域半径内的点数
    expand_radius : int
        扩展半径
    min_region_size : int
        最小区域大小
    max_region_size : int
        最大区域大小
    """
    
    print("=== 选择空间区域 ===")
    
    if 'spatial' not in adata.obsm:
        print("警告：未找到空间坐标信息，随机选择区域")
        n_spots = adata.n_obs
        region_size = min(max_region_size, max(min_region_size, n_spots // 4))
        selected_indices = np.random.choice(n_spots, size=region_size, replace=False)
        selected_mask = np.zeros(n_spots, dtype=bool)
        selected_mask[selected_indices] = True
        adata.obs['selected_region'] = selected_mask
        print(f"随机选择了 {region_size} 个spots")
        return adata
    
    spatial_coords = adata.obsm['spatial']
    
    # 选择中心点
    if center_idx is None:
        center_idx = np.random.randint(0, adata.n_obs)
        print(f"随机选择中心点: {center_idx}")
    else:
        print(f"使用指定中心点: {center_idx}")
    
    center_coord = spatial_coords[center_idx]
    
    # 计算所有点到中心点的距离
    distances = np.linalg.norm(spatial_coords - center_coord, axis=1)
    
    # 选择最近的n_radius个点作为核心区域
    core_indices = np.argsort(distances)[:n_radius]
    
    # 扩展区域
    core_coords = spatial_coords[core_indices]
    expanded_indices = set(core_indices)
    
    for core_idx in core_indices:
        core_coord = spatial_coords[core_idx]
        core_distances = np.linalg.norm(spatial_coords - core_coord, axis=1)
        nearby_indices = np.where(core_distances <= expand_radius)[0]
        expanded_indices.update(nearby_indices)
    
    expanded_indices = list(expanded_indices)
    
    # 调整区域大小
    if len(expanded_indices) < min_region_size:
        # 如果区域太小，继续扩展
        all_distances = np.linalg.norm(spatial_coords - center_coord, axis=1)
        sorted_indices = np.argsort(all_distances)
        expanded_indices = sorted_indices[:min_region_size].tolist()
        print(f"区域太小，扩展到 {min_region_size} 个spots")
    
    elif len(expanded_indices) > max_region_size:
        # 如果区域太大，选择最近的点
        region_coords = spatial_coords[expanded_indices]
        region_distances = np.linalg.norm(region_coords - center_coord, axis=1)
        closest_indices = np.argsort(region_distances)[:max_region_size]
        expanded_indices = [expanded_indices[i] for i in closest_indices]
        print(f"区域太大，缩减到 {max_region_size} 个spots")
    
    # 创建选择mask
    selected_mask = np.zeros(adata.n_obs, dtype=bool)
    selected_mask[expanded_indices] = True
    
    adata.obs['selected_region'] = selected_mask
    
    print(f"选择了 {len(expanded_indices)} 个spots作为分析区域")
    print(f"中心坐标: ({center_coord[0]:.2f}, {center_coord[1]:.2f})")
    
    # 显示选中区域的原始聚类分布
    if 'domain' in adata.obs.columns:
        region_domains = adata.obs[selected_mask]['domain'].value_counts()
        print("选中区域的原始聚类分布:")
        for domain, count in region_domains.items():
            print(f"  {domain}: {count} spots")
    
    return adata

def test_improved_recluster():
    """测试改进后的重聚类功能"""
    
    print("=== 测试改进后的重聚类功能 ===\n")
    
    # 1. 加载数据
    print("1. 加载测试数据...")
    try:
        adata = sc.read_h5ad("adata_domain.h5ad")
        print(f"   数据大小: {adata.shape}")
        print(f"   原始聚类: {adata.obs['domain'].unique()}")
    except Exception as e:
        print(f"   加载数据失败: {e}")
        return
    
    # 2. 选择空间区域
    print("\n2. 选择空间区域...")
    try:
        adata = select_spatial_region(
            adata,
            center_idx=None,     # 随机选择
            n_radius=50,         # 中心区域点数
            expand_radius=30,    # 扩展大小
            min_region_size=50,  # 最小区域大小
            max_region_size=200  # 最大区域大小
        )
        
        selected_count = adata.obs['selected_region'].sum()
        print(f"   选中区域大小: {selected_count} spots")
        
        # 检查选中区域的原始聚类分布
        region_domains = adata.obs[adata.obs['selected_region']]['domain'].value_counts()
        print(f"   选中区域包含的原始聚类:")
        for domain, count in region_domains.items():
            print(f"     {domain}: {count} spots")
            
    except Exception as e:
        print(f"   选择区域失败: {e}")
        return
    
    # 3. 运行改进的重聚类分析
    print("\n3. 运行改进的重聚类分析...")
    print("   配置参数:")
    print("   • min_cluster_size: 10 (提高阈值确保生物学意义)")
    print("   • 特征基因计算: One-vs-rest策略")
    print("   • 小聚类处理: 自动重新分配")
    
    try:
        adata_result = apply_recluster_analysis(
            adata, 
            adata.obs['selected_region'],
            min_cluster_size=10  # 明确指定较高的阈值
        )
        print("   ✅ 重聚类分析完成")
        
    except Exception as e:
        print(f"   ❌ 重聚类分析失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print("\n=== 测试完成 ===")
    return adata_result

def _compute_single_cluster_features_fallback(region_adata, cluster_mask):
    """当差异表达分析失败时的回退方法"""
    
    cluster_data = region_adata[cluster_mask]
    
    if cluster_data.n_obs == 0:
        return {'method': 'empty', 'signature': None}
    
    # 获取基因表达数据
    if hasattr(cluster_data.X, 'toarray'):
        X = cluster_data.X.toarray()
    else:
        X = cluster_data.X
    
    # 计算基本统计量
    mean_expr = np.mean(X, axis=0)
    std_expr = np.std(X, axis=0)
    
    # 选择变异最大的基因作为特征基因
    gene_vars = np.var(X, axis=0)
    top_var_indices = np.argsort(gene_vars)[-200:]  # 取前200个变异最大的基因
    
    # 获取基因名称
    top_genes = region_adata.var.index[top_var_indices]
    
    # 创建基因信息字典
    gene_info = {}
    for j, gene in enumerate(top_genes):
        gene_info[gene] = {
            'score': gene_vars[top_var_indices[j]],
            'rank': j,
            'pval': 1.0,
            'pval_adj': 1.0,
            'logfoldchange': 0.0
        }
    
    return {
        'method': 'variance_based',
        'mean_expression': mean_expr,
        'std_expression': std_expr,
        'deg_genes': list(top_genes),
        'deg_gene_info': gene_info,
        'n_spots': cluster_data.n_obs,
        'n_deg_genes': len(top_genes)
    }

def _compute_new_cluster_features_fallback(region_adata, cluster_results):
    """当差异表达分析完全失败时的回退方法"""
    
    print("使用回退方法计算聚类特征...")
    
    hard_labels = cluster_results['hard_labels']
    n_components = cluster_results['n_components']
    
    new_cluster_features = {}
    
    for i in range(n_components):
        cluster_mask = hard_labels == i
        new_cluster_features[i] = _compute_single_cluster_features_fallback(region_adata, cluster_mask)
        
        if new_cluster_features[i]['method'] != 'empty':
            print(f"  聚类 {i}: 使用变异基因作为特征 ({new_cluster_features[i]['n_deg_genes']} 个基因)")
    
    return new_cluster_features

def _compute_original_cluster_features_fallback(full_adata, all_original_clusters):
    """当原始聚类的差异表达分析失败时的回退方法"""
    
    print("使用回退方法计算原始聚类特征...")
    
    original_features = {}
    
    for cluster_id in all_original_clusters:
        cluster_mask = full_adata.obs['domain'] == cluster_id
        cluster_data = full_adata[cluster_mask]
        
        if cluster_data.n_obs == 0:
            continue
        
        print(f"  处理原始聚类 {cluster_id} ({cluster_data.n_obs} spots)")
        
        # 获取基因表达数据
        if hasattr(cluster_data.X, 'toarray'):
            X = cluster_data.X.toarray()
        else:
            X = cluster_data.X
        
        # 计算基本统计量
        mean_expr = np.mean(X, axis=0)
        std_expr = np.std(X, axis=0)
        
        # 选择变异最大的基因作为特征基因
        gene_vars = np.var(X, axis=0)
        top_var_indices = np.argsort(gene_vars)[-200:]  # 取前200个变异最大的基因
        
        # 获取基因名称
        top_genes = full_adata.var.index[top_var_indices]
        
        # 创建基因信息字典
        gene_info = {}
        for j, gene in enumerate(top_genes):
            gene_info[gene] = {
                'score': gene_vars[top_var_indices[j]],
                'rank': j,
                'pval': 1.0,
                'pval_adj': 1.0,
                'logfoldchange': 0.0
            }
        
        original_features[cluster_id] = {
            'method': 'variance_based',
            'mean_expression': mean_expr,
            'std_expression': std_expr,
            'deg_genes': list(top_genes),
            'deg_gene_info': gene_info,
            'n_spots': cluster_data.n_obs,
            'n_deg_genes': len(top_genes)
        }
        
        print(f"    使用变异基因作为特征 ({len(top_genes)} 个基因)")
    
    return original_features

def _fallback_simple_selection(X_scaled, n_components_range, sample_weight=None):
    """备用的简单选择方法"""
    
    print("使用备用的简单选择方法...")
    
    best_k = n_components_range[0]
    best_score = -1
    best_model = None
    
    for k in range(n_components_range[0], min(n_components_range[1] + 1, 6)):
        try:
            model = GaussianMixture(n_components=k, random_state=42, 
                                  covariance_type='diag', max_iter=100)
            X_train = _expand_training_data_by_weight(X_scaled, sample_weight)
            model.fit(X_train)
            labels = model.predict(X_scaled)
            
            if len(np.unique(labels)) >= 2:
                score = silhouette_score(X_scaled, labels)
                print(f"K={k}: 轮廓系数={score:.3f}")
                
                if score > best_score:
                    best_score = score
                    best_k = k
                    best_model = model
            
        except Exception as e:
            print(f"K={k} 失败: {e}")
            continue
    
    if best_model is None:
        print("所有方法都失败，使用K=2的简单模型")
        best_k = 2
        best_model = GaussianMixture(n_components=2, random_state=42, 
                                   covariance_type='diag', max_iter=50)
        X_train = _expand_training_data_by_weight(X_scaled, sample_weight)
        best_model.fit(X_train)
    
    simple_results = pd.DataFrame([{
        'n_components': best_k,
        'silhouette_score': best_score,
        'method': 'fallback_simple'
    }])
    
    return best_model, best_k, simple_results

if __name__ == "__main__":
    # NOTE: do not hard-code R_HOME paths here (breaks on macOS/other machines).
    section_id = "151673"
    n_clusters = 7
    # file_fold ='data/'+ str(section_id)
    # adata = sc.read_visium(file_fold, count_file= 'filtered_feature_bc_matrix.h5', load_images=True)
    # ## read ground truth
    # Ann_df = pd.read_csv("data/" + str(section_id) + "/" + str(section_id)+ "_truth.txt", sep='\t', header=None, index_col=0)
    # Ann_df.columns = ['Ground Truth']
    # adata.obs['ground_truth'] = Ann_df.loc[adata.obs_names, 'Ground Truth']
    # adata = adata[~pd.isnull(adata.obs['ground_truth'])]

    # # adata.var_names_make_unique()
    # # sc.pp.highly_variable_genes(adata, flavor="seurat_v3", n_top_genes=3000)
    # # sc.pp.normalize_total(adata, target_sum=1e4)
    # # sc.pp.log1p(adata)
    # model = GraphST.GraphST(adata, device=device)
    # # train model
    # adata = model.train()
    # # set radius to specify the number of neighbors considered during refinement
    # radius = 50
    # tool = 'mclust'
    # clustering(adata, n_clusters, radius=radius, method=tool, refinement=True)

    # if "emb" in adata.obsm:
    #     print("✅ GraphST 输出维度:", adata.obsm["emb"].shape)
    #     print(adata)
    # else:
    #     print("❌ 没有发现 obsm['emb']，聚类可能失败")

    # adata_Vars = adata[:,adata.var['highly_variable']]
    # adata.write("adata_graphst.h5ad")
    adata = sc.read_h5ad("adata_graphst.h5ad")
    
    selected_barcodes = ['AAATGCTCGTTACGTT-1', 'AACTTCTGCGTCTATC-1', 'ATAGCTGCTCTTGTTA-1', 'CACATGTTTGGACATG-1', 'CACTTCGTCTTATCTC-1', 'CATCCGCAGGCCCGAA-1', 'CATTCCCTAAGTACAA-1', 'CATTGTGTGCTAGATC-1', 'CCATATTGGATCATGA-1', 'CCCTGATGTAACTCGT-1', 'CCCTGTTGGCAAAGAC-1', 'CGCGGCAGTATTACGG-1', 'CGTCGCGGCGGGATTT-1', 'GAAATGGGATGTAAAC-1', 'GAGGAGATCCTCATGC-1', 'GCGAGAAACGGGAGTT-1', 'GGCGGTTTGCCGGTGC-1', 'GGTCCTTCATACGACT-1', 'GTATAATCTCCCGGAT-1', 'GTCATCTCCTACAGCT-1', 'GTGCCTAGCTATGCTT-1', 'TAGTCCGTATGCATAA-1', 'TCACAGATCCTCAAAC-1', 'TGCGTGATTGGGTGTC-1', 'TTCGCATCCGGAAGCA-1', 'AACTCGATGGCGCAGT-1', 'AACTTGCGTTCTCGCG-1', 'AATTACTCGTACGCTC-1', 'ACCACTGTTCAAGAAG-1', 'ACCCTATAGGACTGAG-1', 'AGATGCTATAACGAGC-1', 'AGTTGACATCGGCTGG-1', 'ATACTCTCGCCACTCT-1', 'ATTCCTTCCAGGCGGT-1', 'ATTGATAGCAACGAGA-1', 'CCAAAGCAGTTGGTTG-1', 'CCAGTCCATTATTCGA-1', 'CCATAGTCAGTAACCC-1', 'CCCTAGTGTCAGGTGT-1', 'CGGGCCATAGCCGCAC-1', 'CGTAAACGCTTGAGTG-1', 'CGTCAATCTTTAACAT-1', 'CTTGAGGTTATCCCGA-1', 'GGGTAATGCTGTGTTT-1', 'GTACGACGGCGCTGCG-1', 'TCTCGGCTCCAGGACT-1', 'TGTCGGCATGGTGGAA-1', 'TTAAACAGAGTCCCGC-1', 'TTACCGCCTTAGGGAA-1', 'TTAGATAGGTCGATAC-1', 'TTCCTTTCTGTGTTGC-1']
    selected_mask = adata.obs.index.isin(selected_barcodes)
    print(f"匹配到的spots数量: {selected_mask.sum()}")
    
    if selected_mask.sum() == 0:
        raise ValueError("没有找到匹配的条形码")
    
    # 2. 在adata中添加选中区域标记
    adata.obs['selected_region'] = selected_mask
    adata = adata[:,adata.var['highly_variable']]
    adata_updated = improved_admixture_reclustering(
            adata,
            region_key = 'selected_region',
            n_components_range = (1,8),  # 自动设置
            use_spatial = True,
            spatial_weight = 0.3,
            feature_key = "emb",  # 使用GraphST的embedding
            confidence_threshold=0.7,
            min_cluster_size = 5,  # 适中的最小聚类大小
            use_hvg = True,
            n_hvg = 200,
            mapping_strategy = 'feature_functional'
        )
