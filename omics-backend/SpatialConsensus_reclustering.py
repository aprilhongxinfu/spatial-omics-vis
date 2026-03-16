"""
Spatial Consensus Correction Reclustering Algorithm
基于空间共识校正的重聚类算法

参考 STEAM (Spatial Transcriptomics Ensemble Analysis Method) 的空间共识校正方法
通过利用局部空间共识来改善细胞类型注释的准确性

主要特点：
1. 基于k近邻（默认k=8）构建空间邻域
2. 只考虑至少有min_valid_neighbors个有效邻居的细胞
3. 通过邻居投票来确定聚类标签
4. 主要校正需要60%邻居共识，次要和第三级校正使用更宽松的阈值（>30%）
5. 支持多级投票（默认最多3级）

Author: Generated based on STEAM methodology
Date: 2025-01-24
"""

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import silhouette_score, adjusted_rand_score
from scipy.spatial.distance import cdist
from typing import Tuple, Dict, List, Optional, Union
import json
import scanpy as sc
import warnings
from collections import Counter
warnings.filterwarnings('ignore')


def _get_truth_accuracy_helpers():
    """延迟导入，避免与 Admixture 的循环依赖；复用同一套 truth 映射与准确率计算。"""
    from Admixture_reclustering_v2 import _load_truth_labels, _accuracy_vs_truth
    return _load_truth_labels, _accuracy_vs_truth


def extract_spatial_coordinates(adata, spatial_key: str = 'spatial') -> np.ndarray:
    """
    从AnnData对象中提取空间坐标
    
    Parameters:
    -----------
    adata : AnnData
        空间转录组数据
    spatial_key : str
        空间坐标在obsm中的键名
        
    Returns:
    --------
    np.ndarray
        空间坐标数组 (n_cells, 2)
    """
    if spatial_key in adata.obsm:
        coords = adata.obsm[spatial_key]
        if isinstance(coords, pd.DataFrame):
            coords = coords.values
        return coords[:, :2]  # 只取x, y坐标
    
    # 尝试其他可能的键名
    possible_keys = ['X_spatial', 'spatial_coords', 'array_row', 'array_col']
    for key in possible_keys:
        if key in adata.obsm:
            return adata.obsm[key][:, :2]
    
    # 如果obs中有坐标列
    if 'array_row' in adata.obs.columns and 'array_col' in adata.obs.columns:
        return np.column_stack([
            adata.obs['array_col'].values,
            adata.obs['array_row'].values
        ])
    
    raise ValueError(f"无法找到空间坐标。请确保adata.obsm中包含'{spatial_key}'键")


def build_spatial_neighborhood(
    coordinates: np.ndarray,
    k_neighbors: int = 8,
    metric: str = 'euclidean'
) -> Tuple[np.ndarray, np.ndarray]:
    """
    构建空间邻域图
    
    Parameters:
    -----------
    coordinates : np.ndarray
        空间坐标 (n_cells, 2)
    k_neighbors : int
        邻居数量，默认8
    metric : str
        距离度量，默认欧氏距离
        
    Returns:
    --------
    Tuple[np.ndarray, np.ndarray]
        邻居索引和距离
    """
    n_samples = coordinates.shape[0]
    k = min(k_neighbors + 1, n_samples)  # +1 因为包含自身
    
    nbrs = NearestNeighbors(n_neighbors=k, metric=metric)
    nbrs.fit(coordinates)
    distances, indices = nbrs.kneighbors(coordinates)
    
    # 移除自身（第一个邻居是自己）
    return indices[:, 1:], distances[:, 1:]


def get_neighbor_labels(
    cell_idx: int,
    neighbor_indices: np.ndarray,
    labels: np.ndarray,
    selected_mask: np.ndarray = None
) -> List:
    """
    获取细胞邻居的标签
    
    Parameters:
    -----------
    cell_idx : int
        细胞索引
    neighbor_indices : np.ndarray
        邻居索引数组
    labels : np.ndarray
        所有细胞的标签
    selected_mask : np.ndarray, optional
        选中区域的mask，如果提供则只考虑选中区域内的邻居
        
    Returns:
    --------
    List
        邻居标签列表
    """
    neighbors = neighbor_indices[cell_idx]
    neighbor_labels = []
    
    for n_idx in neighbors:
        if n_idx >= 0 and n_idx < len(labels):
            # 如果提供了selected_mask，只考虑选中区域内的邻居
            if selected_mask is not None:
                if selected_mask[n_idx]:
                    neighbor_labels.append(labels[n_idx])
            else:
                neighbor_labels.append(labels[n_idx])
    
    return neighbor_labels


def get_neighbor_label_weights(
    cell_idx: int,
    neighbor_indices: np.ndarray,
    labels: np.ndarray,
    selected_mask: np.ndarray,
    in_region_weight: float = 1.0,
    out_region_weight: float = 0.5
) -> List[Tuple]:
    """
    获取邻居标签及权重：选中区域内邻居权重 in_region_weight，区域外邻居权重 out_region_weight。
    用于加权投票，避免区域外邻居把局部意图冲掉。
    
    Returns:
        List of (label, weight) tuples.
    """
    neighbors = neighbor_indices[cell_idx]
    out = []
    for n_idx in neighbors:
        if n_idx < 0 or n_idx >= len(labels):
            continue
        w = in_region_weight if selected_mask[n_idx] else out_region_weight
        out.append((labels[n_idx], w))
    return out


def compute_label_consensus(
    neighbor_labels: List,
    current_label,
    max_voting_level: int = 3,
    primary_threshold: float = 0.6,
    secondary_threshold: float = 0.3,
    current_label_low_threshold: float = 0.4
) -> Dict:
    """
    计算邻居标签的共识。
    
    voting_level 定义：先把 current_label 从候选中剔除，再对“替代候选”按频率排序；
    替代候选的第 1 名 = voting_level=1（一级），第 2 名 = 2（二级），第 3 名 = 3（三级）。
    一级用 primary_threshold 判断，二级/三级用 secondary_threshold 判断。
    
    校正规则：候选不仅过阈值，且（优于当前标签 或 当前标签支持度 < current_label_low_threshold）时才改。
    
    Parameters:
    -----------
    neighbor_labels : List
        邻居标签列表。可为 List[label]（等价权重 1）或 List[(label, weight)] 做加权投票。
    current_label :
        当前细胞的标签
    max_voting_level : int
        最大投票级别，默认3
    primary_threshold : float
        主要校正的阈值，默认0.6 (60%)
    secondary_threshold : float
        次要校正的阈值，默认0.3 (30%)
    current_label_low_threshold : float
        当前标签比例低于此值时才允许用次/三级候选覆盖，默认0.4
        
    Returns:
    --------
    Dict
        包含投票结果和置信度的字典
    """
    if len(neighbor_labels) == 0:
        return {
            'suggested_label': current_label,
            'confidence': 0.0,
            'voting_level': 0,
            'consensus_ratio': 0.0,
            'top_label_ratio': 0.0,
            'suggested_label_ratio': 0.0,
            'should_correct': False,
            'label_distribution': {}
        }
    
    # 支持加权：neighbor_labels 可为 List[(label, weight)]，或 List[label]（等价权重 1）
    weighted = (
        len(neighbor_labels) > 0
        and isinstance(neighbor_labels[0], (list, tuple))
        and len(neighbor_labels[0]) == 2
        and isinstance(neighbor_labels[0][1], (int, float))
    )
    if weighted:
        label_counts = {}  # label -> total weight
        total_weight = 0.0
        for label, w in neighbor_labels:
            label_counts[label] = label_counts.get(label, 0.0) + float(w)
            total_weight += float(w)
        total_neighbors = total_weight
    else:
        label_counts = Counter(neighbor_labels)
        total_neighbors = float(len(neighbor_labels))
    
    # 每个标签的（加权）比例
    label_distribution = {label: v / total_neighbors for label, v in label_counts.items()}
    current_ratio = label_distribution.get(current_label, 0.0)
    
    # 剔除 current_label，按加权和排序；第 1 名 = voting_level=1
    alternatives = [(label, label_counts[label]) for label in label_counts if label != current_label]
    sorted_alternatives = sorted(alternatives, key=lambda x: -x[1])[:max_voting_level]
    
    suggested_label = current_label
    confidence = 0.0
    voting_level = 0
    should_correct = False
    
    for level, (label, weight_sum) in enumerate(sorted_alternatives, 1):
        ratio = weight_sum / total_neighbors
        # 一级替代：用 primary_threshold 判断；未通过则继续看二级/三级
        if level == 1:
            if ratio >= primary_threshold and (ratio > current_ratio or current_ratio < current_label_low_threshold):
                suggested_label = label
                confidence = ratio
                voting_level = 1
                should_correct = True
                break
            continue
        # 二级/三级替代：用更宽松的 secondary_threshold 判断，采纳后 break，否则继续下一替代
        if ratio >= secondary_threshold and (ratio > current_ratio or current_ratio < current_label_low_threshold):
            suggested_label = label
            confidence = ratio
            voting_level = level
            should_correct = True
            break
    
    # 对用户/UI 有意义的比例（均为加权比例）
    top_label = max(label_counts.keys(), key=lambda l: label_counts[l]) if label_counts else None
    top_label_ratio = (label_counts[top_label] / total_neighbors) if top_label is not None else 0.0
    suggested_label_ratio = confidence
    
    return {
        'suggested_label': suggested_label,
        'confidence': confidence,
        'voting_level': voting_level,
        'consensus_ratio': suggested_label_ratio,
        'top_label_ratio': top_label_ratio,
        'suggested_label_ratio': suggested_label_ratio,
        'should_correct': should_correct,
        'label_distribution': label_distribution
    }


def _get_neighbor_labels_for_consensus(
    cell_idx: int,
    neighbor_indices: np.ndarray,
    labels: np.ndarray,
    selected_mask: np.ndarray,
    consider_extended_neighbors: bool,
    extended_neighbor_weight: float,
) -> Tuple[List, int]:
    """获取用于共识计算的邻居标签（基于当前 labels），返回 (neighbor_labels, n_valid_for_check)。"""
    if consider_extended_neighbors:
        neighbor_labels = get_neighbor_label_weights(
            cell_idx, neighbor_indices, labels, selected_mask,
            in_region_weight=1.0,
            out_region_weight=extended_neighbor_weight
        )
        n_in_region = len(get_neighbor_labels(
            cell_idx, neighbor_indices, labels, selected_mask=selected_mask
        ))
        n_valid_for_check = n_in_region
    else:
        neighbor_labels = get_neighbor_labels(
            cell_idx, neighbor_indices, labels, selected_mask=selected_mask
        )
        neighbor_labels = [(l, 1.0) for l in neighbor_labels]
        n_valid_for_check = len(neighbor_labels)
    return neighbor_labels, n_valid_for_check


# ---------------------------------------------------------------------------
# 多结果分布 + 空间传播（Refine：投票候选分布而非 hard label）
# ---------------------------------------------------------------------------

def build_spot_label_distributions(
    labels_list: List[Union[np.ndarray, pd.Series]],
    result_weights: Optional[List[float]] = None,
    n_obs: Optional[int] = None,
) -> Tuple[np.ndarray, List]:
    """
    从多份聚类结果为每个 spot 构建候选标签分布 P_i(label) = freq(label)。
    
    Parameters:
    -----------
    labels_list : List[array-like]
        多份结果，每份为长度 n_obs 的标签数组（与 adata.obs 顺序一致）
    result_weights : List[float], optional
        每份结果的权重 w_m；若为 None 则等权
    n_obs : int, optional
        spot 数量；若不提供则取 len(labels_list[0])
        
    Returns:
    --------
    P_flat : np.ndarray, shape (n_obs, n_labels)
        每行和为 1 的分布
    all_labels : List
        全体出现过的标签的有序列表，P_flat 的列与之对应
    """
    if not labels_list:
        raise ValueError("labels_list 不能为空")
    M = len(labels_list)
    if n_obs is None:
        n_obs = len(np.asarray(labels_list[0]).ravel())
    weights = result_weights
    if weights is None:
        weights = [1.0] * M
    if len(weights) != M:
        weights = [1.0] * M
    
    # 收集所有出现过的标签
    all_labels_set = set()
    for arr in labels_list:
        a = np.asarray(arr).ravel()
        for v in a:
            try:
                all_labels_set.add(str(v))
            except Exception:
                all_labels_set.add(str(v))
    all_labels = sorted(list(all_labels_set), key=str)
    label_to_idx = {label: i for i, label in enumerate(all_labels)}
    n_labels = len(all_labels)
    
    P_flat = np.zeros((n_obs, n_labels))
    for m, arr in enumerate(labels_list):
        a = np.asarray(arr).ravel()
        if len(a) != n_obs:
            raise ValueError(f"第 {m} 份结果长度 {len(a)} 与 n_obs={n_obs} 不一致")
        w = float(weights[m])
        for i in range(n_obs):
            lab = str(a[i])
            if lab in label_to_idx:
                P_flat[i, label_to_idx[lab]] += w
    # 行归一化
    row_sum = P_flat.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0] = 1.0
    P_flat = P_flat / row_sum
    return P_flat, all_labels


def smooth_distributions_spatial(
    P_flat: np.ndarray,
    neighbor_indices: np.ndarray,
    selected_mask: np.ndarray,
    alpha: float = 0.5,
    max_iter: int = 20,
    consider_extended_neighbors: bool = True,
    in_region_weight: float = 1.0,
    out_region_weight: float = 0.5,
    tol: float = 1e-6,
) -> np.ndarray:
    """
    对分布做邻域平均（空间一致性）：P_i_new = normalize( α*P_i + (1-α)*mean_j P_j )。
    只更新 selected_mask 为 True 的 spot；邻居可含选区外，可选加权。
    
    Parameters:
    -----------
    P_flat : np.ndarray, shape (n_obs, n_labels)
    neighbor_indices : np.ndarray
        来自 build_spatial_neighborhood 的 indices
    selected_mask : np.ndarray, bool
    alpha : float
        自身权重，邻域平均权重为 (1-alpha)
    max_iter : int
    consider_extended_neighbors : bool
        若 True，邻域平均时区域外邻居权重 out_region_weight
    in_region_weight, out_region_weight : float
    tol : float
        相邻两次更新最大差小于 tol 则提前收敛
        
    Returns:
    --------
    P_new : np.ndarray, shape (n_obs, n_labels)
    """
    n_obs, n_labels = P_flat.shape
    P = P_flat.copy()
    selected_indices = np.where(selected_mask)[0]
    
    for it in range(max_iter):
        P_next = P.copy()
        max_delta = 0.0
        for i in selected_indices:
            neighbors = neighbor_indices[i]
            if consider_extended_neighbors:
                weights = np.where(selected_mask[neighbors], in_region_weight, out_region_weight)
                weights = weights.astype(float)
                total_w = weights.sum()
                if total_w <= 0:
                    continue
                mean_j = np.zeros(n_labels)
                for k, j in enumerate(neighbors):
                    mean_j += weights[k] * P[j]
                mean_j /= total_w
            else:
                in_nbrs = neighbors[selected_mask[neighbors]]
                if len(in_nbrs) == 0:
                    continue
                mean_j = P[in_nbrs].mean(axis=0)
            blended = alpha * P[i] + (1.0 - alpha) * mean_j
            blended = np.maximum(blended, 0.0)
            s = blended.sum()
            if s > 0:
                blended = blended / s
            delta = np.abs(blended - P_next[i]).max()
            max_delta = max(max_delta, delta)
            P_next[i] = blended
        P = P_next
        if max_delta < tol:
            break
    return P


def refine_by_multi_result_distribution(
    adata,
    labels_list: List[Union[np.ndarray, pd.Series]],
    region_key: str = 'selected_region',
    spatial_key: str = 'spatial',
    k_neighbors: int = 8,
    result_weights: Optional[List[float]] = None,
    smooth_alpha: float = 0.5,
    smooth_max_iter: int = 20,
    stability_threshold: float = 0.95,
    confidence_strong: float = 0.7,
    alt_gap_strong: float = 0.15,
    consider_extended_neighbors: bool = True,
    out_region_weight: float = 0.5,
    domain_key: str = 'domain',
) -> Tuple[List[Dict], Dict]:
    """
    一次 Refine：从多结果构建 P_i → 空间平滑 → 输出建议（不强制写回）。
    返回格式与 spatial_consensus_correction 兼容，便于 integrate_correction_results / prob_matrix / 前端。
    
    Parameters:
    -----------
    adata : AnnData
    labels_list : List[array-like]
        多份聚类结果，每份与 adata.obs 顺序一致
    region_key, spatial_key, domain_key : str
    k_neighbors : int
    result_weights : optional
        每份结果的权重（如 silhouette 越高越可靠）
    smooth_alpha, smooth_max_iter : float, int
        平滑：P_i_new = normalize( α*P_i + (1-α)*mean_neighbor P_j )
    stability_threshold : float
        安全阀 A：stability >= 此值的 spot 不改（避免推翻多结果中极稳定的点）
    confidence_strong, alt_gap_strong : float
        安全阀 B：confidence >= confidence_strong 且 alt_gap >= alt_gap_strong 为 "strong"，否则 "uncertain"
    consider_extended_neighbors, out_region_weight : bool, float
        
    Returns:
    --------
    correction_results : List[Dict]
        每项含 cell_idx, cell_id, original_label, suggested_label, corrected, reason,
        confidence, alt_gap, top3, stability, label_distribution, suggestion_strength
    statistics : Dict
    """
    coordinates = extract_spatial_coordinates(adata, spatial_key)
    if region_key not in adata.obs.columns:
        raise ValueError(f"找不到选中区域 '{region_key}'")
    selected_mask = adata.obs[region_key].astype(bool).values
    n_selected = int(np.sum(selected_mask))
    if n_selected == 0:
        raise ValueError("没有选中任何 spots")
    
    n_obs = adata.n_obs
    original_labels = adata.obs[domain_key].values if domain_key in adata.obs.columns else None
    if original_labels is None:
        original_labels = np.asarray(labels_list[0]).ravel().copy()
    
    # 1) 多结果候选池
    P_flat, all_labels = build_spot_label_distributions(
        labels_list, result_weights=result_weights, n_obs=n_obs
    )
    label_to_idx = {l: i for i, l in enumerate(all_labels)}
    selected_indices = np.where(selected_mask)[0]
    
    # Refine 诊断：打印候选池与稳定性信息
    print("\n--- Refine 诊断（选区内）---")
    for m, arr in enumerate(labels_list):
        lab_arr = np.asarray(arr).ravel()
        sel_labels = lab_arr[selected_indices]
        n_unique = len(np.unique(sel_labels))
        print(f"  result[{m}] 选区内 unique labels 数量: {n_unique}  (若全为 1 则候选池无多样性)")
    in_region = P_flat[selected_indices]  # (n_selected, n_labels)
    n_nonzero_cols = (in_region > 0).any(axis=0).sum()
    print(f"  选区内 P_flat 非零列数（label 多样性）: {n_nonzero_cols}  (若为 1 则多结果一致但可能一致错)")
    stab_sel = np.max(P_flat, axis=1)[selected_indices]
    print(f"  稳定性分布 (max prob): min={stab_sel.min():.3f}, max={stab_sel.max():.3f}, mean={stab_sel.mean():.3f}")
    for low, high in [(0.0, 0.5), (0.5, 0.9), (0.9, 1.0)]:
        cnt = np.sum((stab_sel >= low) & (stab_sel < high))
        print(f"    [{low:.1f}, {high:.1f}): {cnt} spots")
    print("---\n")
    
    # 稳定性：原始分布的最大概率
    stability_per_spot = np.max(P_flat, axis=1)
    
    # 2) 空间平滑（仅选区内更新）
    neighbor_indices, _ = build_spatial_neighborhood(coordinates, k_neighbors=k_neighbors)
    P_smooth = smooth_distributions_spatial(
        P_flat,
        neighbor_indices,
        selected_mask,
        alpha=smooth_alpha,
        max_iter=smooth_max_iter,
        consider_extended_neighbors=consider_extended_neighbors,
        out_region_weight=out_region_weight,
    )
    
    # 3) 每个 spot：new_label, confidence, alt_gap, top3
    selected_indices = np.where(selected_mask)[0]
    correction_results = []
    n_corrected = 0
    n_stable_unchanged = 0
    n_strong = 0
    n_uncertain = 0
    
    for pos, cell_idx in enumerate(selected_indices):
        p = P_smooth[cell_idx]
        orig = original_labels[cell_idx] if original_labels is not None else None
        orig_str = str(orig) if orig is not None else (all_labels[0] if all_labels else "unknown")
        
        # argmax
        idx_max = int(np.argmax(p))
        new_label = all_labels[idx_max]
        confidence = float(p[idx_max])
        # second max for alt_gap
        p_sorted = np.sort(p)[::-1]
        second_max = float(p_sorted[1]) if len(p_sorted) > 1 else 0.0
        alt_gap = confidence - second_max
        
        # top-3
        top_indices = np.argsort(p)[::-1][:3]
        top3 = [(all_labels[i], float(p[i])) for i in top_indices]
        
        stability = float(stability_per_spot[cell_idx])
        label_distribution = {all_labels[j]: float(p[j]) for j in range(len(all_labels)) if p[j] > 0}
        
        # 安全阀 A：多结果中非常稳定的点不改
        if stability >= stability_threshold:
            suggested_label = orig_str
            corrected = False
            reason = "stable_no_change"
            n_stable_unchanged += 1
        else:
            suggested_label = new_label
            corrected = (str(suggested_label) != str(orig_str))
            reason = "distribution_refined"
            if corrected:
                n_corrected += 1
        
        # 安全阀 B：强建议 vs 不确定
        if confidence >= confidence_strong and alt_gap >= alt_gap_strong:
            suggestion_strength = "strong"
            n_strong += 1
        else:
            suggestion_strength = "uncertain"
            n_uncertain += 1
        
        # 兼容原有字段
        correction_results.append({
            'cell_idx': cell_idx,
            'cell_id': adata.obs.index[cell_idx],
            'original_label': orig_str,
            'suggested_label': suggested_label,
            'corrected': corrected,
            'reason': reason,
            'n_valid_neighbors': 0,  # 分布法不按邻居数过滤
            'confidence': confidence,
            'voting_level': 0,
            'consensus_ratio': confidence,
            'top_label_ratio': confidence,
            'suggested_label_ratio': confidence,
            'label_distribution': label_distribution,
            'alt_gap': alt_gap,
            'top3': top3,
            'stability': stability,
            'suggestion_strength': suggestion_strength,
        })
    
    statistics = {
        'n_selected': n_selected,
        'n_corrected': n_corrected,
        'n_stable_unchanged': n_stable_unchanged,
        'n_strong': n_strong,
        'n_uncertain': n_uncertain,
        'correction_rate': n_corrected / n_selected if n_selected > 0 else 0,
        'n_results': len(labels_list),
        'smooth_alpha': smooth_alpha,
        'smooth_max_iter': smooth_max_iter,
        'stability_threshold': stability_threshold,
        'k_neighbors': k_neighbors,
    }
    return correction_results, statistics


def spatial_consensus_correction(
    adata,
    region_key: str = 'selected_region',
    k_neighbors: int = 8,
    min_valid_neighbors: int = 3,
    primary_threshold: float = 0.6,
    secondary_threshold: float = 0.3,
    max_voting_level: int = 3,
    current_label_low_threshold: float = 0.4,
    domain_key: str = 'domain',
    spatial_key: str = 'spatial',
    consider_extended_neighbors: bool = True,
    extended_neighbor_weight: float = 0.5,
    max_iterations: int = 10,
    min_change_ratio: float = 1e-4,
    apply_top_fraction_per_iter: float = 1.0,
) -> Tuple:
    """
    空间共识校正的主函数（迭代 STEAM 风格：每轮基于当前 labels_t 生成建议，排序后分波应用，再更新 labels_t）。
    
    Parameters:
    -----------
    adata : AnnData
        空间转录组数据，包含原始聚类结果
    region_key : str
        选中区域在obs中的键名
    k_neighbors : int
        邻居数量，默认8
    min_valid_neighbors : int
        最小有效邻居数，默认3。consider_extended_neighbors 为 True 时指「选中区域内」的邻居数。
    primary_threshold : float
        主要校正阈值，默认0.6
    secondary_threshold : float
        次要校正阈值，默认0.3
    max_voting_level : int
        最大投票级别，默认3
    current_label_low_threshold : float
        当前标签比例低于此值时才允许用次/三级候选覆盖，默认0.4
    domain_key : str
        聚类标签在obs中的键名
    spatial_key : str
        空间坐标在obsm中的键名
    consider_extended_neighbors : bool
        是否考虑选中区域外的邻居
    extended_neighbor_weight : float
        选中区域外邻居的权重
    max_iterations : int
        最大迭代轮数；每轮：生成候选 -> 排序 -> 应用 top 比例 -> 更新 labels_t
    min_change_ratio : float
        若本轮 applied/n_selected < 此值则提前停止
    apply_top_fraction_per_iter : float
        每轮只应用排序后候选的前此比例（0~1），1.0 表示全部应用（分波时可用 <1 控制每波量）
        
    Returns:
    --------
    Tuple
        (correction_results, statistics)，statistics 中含 iteration_history
    """
    print("=== 开始空间共识校正分析（迭代 STEAM 风格）===")
    
    # 1. 提取空间坐标
    coordinates = extract_spatial_coordinates(adata, spatial_key)
    print(f"提取空间坐标完成，共 {coordinates.shape[0]} 个细胞")
    
    # 2. 获取选中区域的mask
    if region_key not in adata.obs.columns:
        raise ValueError(f"找不到选中区域标记 '{region_key}'")
    
    selected_mask = adata.obs[region_key].astype(bool).values
    n_selected = np.sum(selected_mask)
    print(f"选中区域包含 {n_selected} 个spots")
    
    if n_selected == 0:
        raise ValueError("没有选中任何spots")
    
    # 3. 获取原始标签，并初始化当前轮标签（核心：每轮共识基于 labels_t，不是 original_labels）
    if domain_key not in adata.obs.columns:
        raise ValueError(f"找不到聚类标签 '{domain_key}'")
    
    original_labels = adata.obs[domain_key].values.copy()
    labels_t = original_labels.copy()
    
    # 4. 构建空间邻域
    neighbor_indices, neighbor_distances = build_spatial_neighborhood(
        coordinates, k_neighbors=k_neighbors
    )
    print(f"构建空间邻域完成，每个细胞有 {k_neighbors} 个邻居")
    
    selected_indices = np.where(selected_mask)[0]
    n_skipped_few_neighbors = 0
    iteration_history = []
    # 每个细胞的有效邻居数（与 labels 无关，只与 selected_mask 有关，首轮即可填满）
    cell_valid_neighbors = {}
    for cell_idx in selected_indices:
        _, n_valid = _get_neighbor_labels_for_consensus(
            cell_idx, neighbor_indices, labels_t, selected_mask,
            consider_extended_neighbors, extended_neighbor_weight
        )
        cell_valid_neighbors[cell_idx] = n_valid
    
    # 记录在迭代中被应用过的细胞所用的 voting_level（用于最终统计）
    applied_cell_voting_level = {}  # cell_idx -> voting_level
    
    # 5. 迭代：每轮对 target cells 生成候选（基于 labels_t），排序（voting_level 优先，再 consensus），应用 top 比例，更新 labels_t
    for iteration in range(1, max_iterations + 1):
        candidates = []  # (cell_idx, suggested_label, voting_level, consensus_ratio, n_valid, full_consensus_dict)
        
        for cell_idx in selected_indices:
            current_label = labels_t[cell_idx]
            neighbor_labels, n_valid_for_check = _get_neighbor_labels_for_consensus(
                cell_idx, neighbor_indices, labels_t, selected_mask,
                consider_extended_neighbors, extended_neighbor_weight
            )
            if n_valid_for_check < min_valid_neighbors:
                continue
            
            consensus = compute_label_consensus(
                neighbor_labels,
                current_label,
                max_voting_level=max_voting_level,
                primary_threshold=primary_threshold,
                secondary_threshold=secondary_threshold,
                current_label_low_threshold=current_label_low_threshold
            )
            if not consensus['should_correct']:
                continue
            suggested = consensus['suggested_label']
            if suggested == current_label:
                continue
            candidates.append((
                cell_idx,
                suggested,
                consensus['voting_level'],
                consensus['consensus_ratio'],
                n_valid_for_check,
                consensus
            ))
        
        # 排序：先 voting_level 升序（1 优先），再 consensus_ratio 降序
        candidates.sort(key=lambda x: (x[2], -x[3]))
        
        # 分波应用：本轮只应用 top apply_top_fraction_per_iter 的候选
        n_apply = max(1, int(len(candidates) * apply_top_fraction_per_iter)) if candidates else 0
        to_apply = candidates[:n_apply]
        applied_this_iter = 0
        for (cell_idx, suggested_label, vl, _cr, _n, _consensus) in to_apply:
            if labels_t[cell_idx] != suggested_label:
                labels_t[cell_idx] = suggested_label
                applied_cell_voting_level[cell_idx] = vl
                applied_this_iter += 1
        
        change_rate = applied_this_iter / n_selected if n_selected > 0 else 0.0
        iteration_history.append({
            'iteration': iteration,
            'n_candidates': len(candidates),
            'n_applied': applied_this_iter,
            'change_rate': change_rate
        })
        print(f"  迭代 {iteration}: 候选 {len(candidates)}, 应用 {applied_this_iter}, 变化率 {change_rate:.4f}")
        
        if applied_this_iter == 0 or change_rate < min_change_ratio:
            print(f"  提前停止（应用数=0 或变化率 < {min_change_ratio}）")
            break
    
    # 6. 用最终 labels_t 做一次共识 pass，仅用于填充每细胞的 confidence / voting_level / label_distribution（供下游展示）
    correction_results = []
    voting_level_counts = {1: 0, 2: 0, 3: 0}
    n_corrected = 0
    
    for cell_idx in selected_indices:
        original_label = original_labels[cell_idx]
        final_label = labels_t[cell_idx]
        corrected = (final_label != original_label)
        if corrected:
            n_corrected += 1
        
        n_valid_for_check = cell_valid_neighbors.get(cell_idx, 0)
        if n_valid_for_check < min_valid_neighbors:
            n_skipped_few_neighbors += 1
            correction_results.append({
                'cell_idx': cell_idx,
                'cell_id': adata.obs.index[cell_idx],
                'original_label': original_label,
                'suggested_label': final_label,
                'corrected': corrected,
                'reason': 'insufficient_neighbors',
                'n_valid_neighbors': n_valid_for_check,
                'confidence': 0.0,
                'voting_level': 0,
                'label_distribution': {}
            })
            continue
        
        neighbor_labels, _ = _get_neighbor_labels_for_consensus(
            cell_idx, neighbor_indices, labels_t, selected_mask,
            consider_extended_neighbors, extended_neighbor_weight
        )
        consensus = compute_label_consensus(
            neighbor_labels,
            final_label,
            max_voting_level=max_voting_level,
            primary_threshold=primary_threshold,
            secondary_threshold=secondary_threshold,
            current_label_low_threshold=current_label_low_threshold
        )
        if corrected:
            vl = applied_cell_voting_level.get(cell_idx, 0)
            if vl in voting_level_counts:
                voting_level_counts[vl] += 1
        
        correction_results.append({
            'cell_idx': cell_idx,
            'cell_id': adata.obs.index[cell_idx],
            'original_label': original_label,
            'suggested_label': final_label,
            'corrected': corrected,
            'reason': 'consensus_correction' if corrected else 'no_consensus',
            'n_valid_neighbors': n_valid_for_check,
            'confidence': consensus['confidence'],
            'voting_level': consensus['voting_level'],
            'consensus_ratio': consensus['consensus_ratio'],
            'top_label_ratio': consensus.get('top_label_ratio', 0.0),
            'suggested_label_ratio': consensus.get('suggested_label_ratio', consensus['confidence']),
            'label_distribution': consensus['label_distribution']
        })
    
    # 7. 汇总统计（含迭代历史）
    statistics = {
        'n_selected': n_selected,
        'n_corrected': n_corrected,
        'n_skipped_few_neighbors': n_skipped_few_neighbors,
        'correction_rate': n_corrected / n_selected if n_selected > 0 else 0,
        'voting_level_distribution': voting_level_counts,
        'k_neighbors': k_neighbors,
        'min_valid_neighbors': min_valid_neighbors,
        'primary_threshold': primary_threshold,
        'secondary_threshold': secondary_threshold,
        'iteration_history': iteration_history,
        'max_iterations': max_iterations,
        'min_change_ratio': min_change_ratio,
        'apply_top_fraction_per_iter': apply_top_fraction_per_iter,
    }
    
    print(f"\n=== 空间共识校正分析完成 ===")
    print(f"  选中spots数: {n_selected}")
    print(f"  校正的spots数: {n_corrected} ({statistics['correction_rate']*100:.1f}%)")
    print(f"  因邻居不足跳过: {n_skipped_few_neighbors}")
    print(f"  投票级别分布: {voting_level_counts}")
    print(f"  迭代轮数: {len(iteration_history)}")
    
    return correction_results, statistics


def integrate_correction_results(
    adata,
    correction_results: List[Dict],
    region_mask: np.ndarray,
    apply_correction: bool = True
):
    """
    将校正结果整合回AnnData对象
    
    Parameters:
    -----------
    adata : AnnData
        原始AnnData对象
    correction_results : List[Dict]
        校正结果列表
    region_mask : np.ndarray
        选中区域的mask
    apply_correction : bool
        是否应用校正结果
        
    Returns:
    --------
    AnnData
        更新后的AnnData对象
    """
    print("开始整合校正结果...")
    
    # 初始化结果列
    if 'domain' in adata.obs.columns:
        original_domain = adata.obs['domain'].astype(str)
    else:
        original_domain = pd.Series(['unknown'] * adata.n_obs, index=adata.obs.index)
    
    # 创建新的结果列
    adata.obs['recluster_hard_labels'] = original_domain.copy()
    adata.obs['recluster_result'] = original_domain.copy()
    adata.obs['recluster_confidence'] = 0.0
    adata.obs['recluster_max_prob'] = 0.0
    adata.obs['label_changed'] = False
    adata.obs['recluster_mapping_source'] = 'spatial_consensus'
    adata.obs['recluster_relationship'] = 'consensus_based'
    adata.obs['recluster_mapping_confidence'] = 0.0
    adata.obs['consensus_voting_level'] = 0
    adata.obs['consensus_n_neighbors'] = 0
    adata.obs['recluster_alt_gap'] = np.nan
    adata.obs['recluster_top3'] = '[]'
    adata.obs['recluster_stability'] = np.nan
    adata.obs['recluster_suggestion_strength'] = ''
    
    # 应用校正结果
    for result in correction_results:
        cell_idx = result['cell_idx']
        cell_id = result['cell_id']
        
        # 更新结果
        adata.obs.loc[cell_id, 'recluster_hard_labels'] = str(result['suggested_label'])
        adata.obs.loc[cell_id, 'recluster_confidence'] = result['confidence']
        adata.obs.loc[cell_id, 'recluster_max_prob'] = result['confidence']
        adata.obs.loc[cell_id, 'consensus_voting_level'] = result['voting_level']
        adata.obs.loc[cell_id, 'consensus_n_neighbors'] = result['n_valid_neighbors']
        
        # 判断标签是否改变
        original_label = str(result['original_label'])
        suggested_label = str(result['suggested_label'])
        label_changed = (original_label != suggested_label) and result['corrected']
        
        adata.obs.loc[cell_id, 'label_changed'] = label_changed
        
        if apply_correction and result['corrected']:
            adata.obs.loc[cell_id, 'recluster_result'] = suggested_label
        else:
            adata.obs.loc[cell_id, 'recluster_result'] = original_label
        
        adata.obs.loc[cell_id, 'recluster_mapping_confidence'] = result['confidence']
        # 多结果分布 refine 的扩展字段（供 UI 展示）
        if 'alt_gap' in result:
            adata.obs.loc[cell_id, 'recluster_alt_gap'] = result['alt_gap']
        if 'top3' in result:
            adata.obs.loc[cell_id, 'recluster_top3'] = json.dumps(result['top3'])
        if 'stability' in result:
            adata.obs.loc[cell_id, 'recluster_stability'] = result['stability']
        if 'suggestion_strength' in result:
            adata.obs.loc[cell_id, 'recluster_suggestion_strength'] = result['suggestion_strength']
    
    # 统计
    n_changed = adata.obs['label_changed'].sum()
    print(f"整合完成:")
    print(f"  选中区域: {region_mask.sum()} spots")
    print(f"  标签改变的spots: {n_changed}")
    
    return adata


def _create_probability_matrix(
    correction_results: List[Dict],
    n_selected: int
) -> Tuple[np.ndarray, List]:
    """
    基于共识结果创建概率矩阵
    
    Parameters:
    -----------
    correction_results : List[Dict]
        校正结果列表
    n_selected : int
        选中的spots数量
        
    Returns:
    --------
    Tuple[np.ndarray, List]
        概率矩阵和标签列表
    """
    # 收集所有可能的标签
    all_labels = set()
    for result in correction_results:
        all_labels.add(result['original_label'])
        all_labels.add(result['suggested_label'])
        for label in result.get('label_distribution', {}).keys():
            all_labels.add(label)
    
    all_labels = sorted(list(all_labels), key=str)
    label_to_idx = {label: idx for idx, label in enumerate(all_labels)}
    n_labels = len(all_labels)
    
    # 创建概率矩阵
    prob_matrix = np.zeros((n_selected, n_labels))
    
    for i, result in enumerate(correction_results):
        label_dist = result.get('label_distribution', {})
        
        if label_dist:
            for label, prob in label_dist.items():
                if label in label_to_idx:
                    prob_matrix[i, label_to_idx[label]] = prob
        else:
            # 如果没有分布信息，给当前标签100%概率
            current_label = result['original_label']
            if current_label in label_to_idx:
                prob_matrix[i, label_to_idx[current_label]] = 1.0
    
    return prob_matrix, all_labels


def generate_recluster_preview_images(
    adata,
    selected_mask: np.ndarray,
    correction_results: List[Dict],
    slice_id: str,
    truth_map: Optional[Dict[str, str]] = None,
    domain_key: str = 'domain',
    spatial_key: str = 'spatial',
) -> Optional[str]:
    """
    生成重聚类预览图片并保存到 data/{slice_id}/recluster_previews/{timestamp}/ 目录下。

    生成三张图片：
    1. ground_truth_preview.png  – 按 ground truth 标签着色，选中 spots 高亮，其余增加透明度
    2. current_result_preview.png – 按当前聚类结果着色，选中 spots 高亮
    3. corrected_result_preview.png – 按修正建议修正后的结果着色，选中 spots 高亮

    Parameters
    ----------
    adata : AnnData
    selected_mask : np.ndarray (bool)
    correction_results : List[Dict]
        校正结果列表，每项包含 cell_idx, suggested_label, corrected 等
    slice_id : str
        切片 ID，用于确定保存路径
    truth_map : Dict[str, str], optional
        barcode -> ground truth label 映射；为 None 时跳过第一张图
    domain_key : str
    spatial_key : str

    Returns
    -------
    str or None
        保存目录的路径，失败时返回 None
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from PIL import Image
        from pathlib import Path
        from datetime import datetime

        print("\n📸 开始生成重聚类预览图片...")

        # ---------- 空间坐标 ----------
        coordinates = extract_spatial_coordinates(adata, spatial_key)

        # ---------- 加载组织切片底图 & scalefactor ----------
        tissue_img = None
        scalefactor = 1.0
        spatial_dir = Path(f"data/{slice_id}/spatial")
        tissue_img_path = spatial_dir / "tissue_hires_image.png"
        sf_path = spatial_dir / "scalefactors_json.json"
        if sf_path.exists():
            with open(sf_path) as _f:
                _sf = json.load(_f)
            scalefactor = _sf.get("tissue_hires_scalef", 1.0)
        if tissue_img_path.exists():
            tissue_img = np.array(Image.open(tissue_img_path))
            print(f"  组织切片底图已加载: {tissue_img_path}  shape={tissue_img.shape}  scalefactor={scalefactor}")
        else:
            print(f"  ⚠️ 未找到组织切片底图: {tissue_img_path}，将使用白色背景")

        # 将坐标转换到 hires image 像素空间
        coords_scaled = coordinates * scalefactor
        x_all = coords_scaled[:, 0]
        y_all = coords_scaled[:, 1]

        # ---------- 计算 figsize ----------
        if tissue_img is not None:
            img_h, img_w = tissue_img.shape[:2]
            aspect = img_h / max(img_w, 1)
            x_min, x_max = 0.0, float(img_w)
            y_min, y_max = 0.0, float(img_h)
        else:
            x_min, x_max = float(x_all.min()), float(x_all.max())
            y_min, y_max = float(y_all.min()), float(y_all.max())
            width = max(x_max - x_min, 1e-6)
            height = max(y_max - y_min, 1e-6)
            aspect = height / width
        base_size = 8
        fig_width = base_size
        fig_height = base_size * aspect

        # ---------- 颜色调色板（与 main.py 中 get_cluster_color_palette 一致） ----------
        _PALETTE = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
            "#393b79", "#637939", "#8c6d31", "#843c39", "#7b4173",
            "#5254a3", "#9c9ede", "#6b6ecf", "#9c755f", "#cedb9c",
        ]

        def _sort_key(name: str):
            try:
                return (0, float(name))
            except ValueError:
                return (1, name)

        def _build_color_map(label_array: np.ndarray) -> Dict:
            unique = sorted(set(str(l) for l in label_array), key=_sort_key)
            mapping = {}
            for i, label in enumerate(unique):
                hex_c = _PALETTE[i % len(_PALETTE)]
                rgb = tuple(int(hex_c[j:j + 2], 16) / 255.0 for j in (1, 3, 5))
                mapping[label] = rgb
            return mapping

        def _build_unified_color_map(*label_arrays: np.ndarray) -> Dict:
            """为多组标签构建统一颜色映射，确保相同 cluster 同色。"""
            all_labels = set()
            for arr in label_arrays:
                all_labels.update(str(l) for l in arr)
            unique = sorted(all_labels, key=_sort_key)
            mapping = {}
            for i, label in enumerate(unique):
                hex_c = _PALETTE[i % len(_PALETTE)]
                rgb = tuple(int(hex_c[j:j + 2], 16) / 255.0 for j in (1, 3, 5))
                mapping[label] = rgb
            return mapping

        # ---------- 绘图辅助函数 ----------
        def _plot_spatial_highlight(
            ax, coords, labels, sel_mask, color_map, title,
            highlight_alpha=1.0, dim_alpha=0.3, spot_size=18
        ):
            """绘制空间散点图：组织切片底图 + 选中 spots 高亮，其余半透明。"""
            # 底图
            if tissue_img is not None:
                ax.imshow(tissue_img, extent=[0, tissue_img.shape[1], tissue_img.shape[0], 0])

            x = coords[:, 0]
            y = coords[:, 1]
            labels_str = np.array([str(l) for l in labels])
            non_sel = ~sel_mask

            # 先绘制非选中点（半透明）
            if non_sel.any():
                for label, color in color_map.items():
                    mask = (labels_str == label) & non_sel
                    if mask.any():
                        ax.scatter(x[mask], y[mask], s=spot_size, color=[color],
                                   alpha=dim_alpha, linewidths=0, edgecolors='none')

            # 再绘制选中点（高亮）
            if sel_mask.any():
                for label, color in color_map.items():
                    mask = (labels_str == label) & sel_mask
                    if mask.any():
                        ax.scatter(x[mask], y[mask], s=spot_size, color=[color],
                                   alpha=highlight_alpha, linewidths=0, edgecolors='none')

            ax.set_xlim(x_min, x_max)
            ax.set_ylim(y_max, y_min)  # 翻转 y 轴
            ax.set_aspect('equal', adjustable='box')
            ax.axis('off')

        def _add_legend(ax, color_map):
            pass  # 不再添加 legend

        # ---------- 保存目录 ----------
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_dir = Path(f"data/{slice_id}/recluster_previews/{timestamp}")
        save_dir.mkdir(parents=True, exist_ok=True)

        sel_mask_bool = selected_mask.astype(bool) if selected_mask.dtype != bool else selected_mask

        # ---------- Truth 标签到 cluster ID 的映射（WM=1, Layer_6=2, ..., Layer_1=7） ----------
        _TRUTH_TO_CID = {
            "WM": "1", "Layer_6": "3", "Layer_5": "2", "Layer_4": "4",
            "Layer_3": "5", "Layer_2": "6", "Layer_1": "7",
        }

        def _truth_label_to_cluster_id(s: str) -> str:
            """将 truth 标签映射到 cluster ID；已是数字直接返回，无法映射返回原值。"""
            s = str(s).strip()
            if s in _TRUTH_TO_CID:
                return _TRUTH_TO_CID[s]
            if s.upper() == "WM":
                return "1"
            if s.startswith("Layer_") and s in _TRUTH_TO_CID:
                return _TRUTH_TO_CID[s]
            if s in ("1", "2", "3", "4", "5", "6", "7"):
                return s
            return s  # 无法映射则保留原值

        # ---------- 准备标签数组 ----------
        current_labels = adata.obs[domain_key].astype(str).values.copy()

        corrected_labels = current_labels.copy()
        for result in correction_results:
            if result['corrected']:
                corrected_labels[result['cell_idx']] = str(result['suggested_label'])

        # ---------- 图 1：Ground Truth 预览（truth 标签映射为 cluster ID，颜色统一） ----------
        if truth_map is not None:
            truth_labels_raw = np.array(['unknown'] * adata.n_obs)
            truth_normalized = {k.strip(): v for k, v in truth_map.items()}
            for i, barcode in enumerate(adata.obs.index):
                bc_key = barcode.strip() if hasattr(barcode, 'strip') else str(barcode)
                if bc_key in truth_normalized:
                    truth_labels_raw[i] = truth_normalized[bc_key]

            # 将 truth 标签映射为 cluster ID，使颜色与 current/corrected 一致
            truth_labels = np.array([
                _truth_label_to_cluster_id(l) if l != 'unknown' else 'unknown'
                for l in truth_labels_raw
            ])

            # 统一 current / corrected / truth(mapped) 的颜色映射
            unified_cmap = _build_unified_color_map(current_labels, corrected_labels, truth_labels)

            fig, ax = plt.subplots(figsize=(fig_width, fig_height))
            _plot_spatial_highlight(ax, coords_scaled, truth_labels, sel_mask_bool,
                                   unified_cmap, '')
            fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
            fig.savefig(save_dir / 'ground_truth_preview.png', dpi=150,
                        bbox_inches='tight', pad_inches=0, facecolor='white')
            plt.close(fig)
            print(f"  ✅ Ground truth 预览图已保存（truth 标签已映射: WM→1, Layer_6→2, ..., Layer_1→7）")
        else:
            # 没有 truth 时，只用 current/corrected 构建统一颜色
            unified_cmap = _build_unified_color_map(current_labels, corrected_labels)

        # ---------- 图 2：当前结果预览 ----------
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))
        _plot_spatial_highlight(ax, coords_scaled, current_labels, sel_mask_bool,
                               unified_cmap, '')
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        fig.savefig(save_dir / 'current_result_preview.png', dpi=150,
                    bbox_inches='tight', pad_inches=0, facecolor='white')
        plt.close(fig)
        print(f"  ✅ 当前结果预览图已保存")

        # ---------- 图 3：修正后结果预览 ----------
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))
        _plot_spatial_highlight(ax, coords_scaled, corrected_labels, sel_mask_bool,
                               unified_cmap, '')
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        fig.savefig(save_dir / 'corrected_result_preview.png', dpi=150,
                    bbox_inches='tight', pad_inches=0, facecolor='white')
        plt.close(fig)
        print(f"  ✅ 修正后结果预览图已保存")

        print(f"📸 三张预览图已保存到: {save_dir}")
        return str(save_dir)

    except Exception as e:
        print(f"⚠️ 生成重聚类预览图片时出错: {e}")
        import traceback
        traceback.print_exc()
        return None


def spatial_consensus_reclustering(
    adata,
    region_key: str = 'selected_region',
    k_neighbors: int = 8,
    min_valid_neighbors: int = 3,
    primary_threshold: float = 0.6,
    secondary_threshold: float = 0.3,
    max_voting_level: int = 3,
    current_label_low_threshold: float = 0.4,
    domain_key: str = 'domain',
    spatial_key: str = 'spatial',
    consider_extended_neighbors: bool = True,
    extended_neighbor_weight: float = 0.5,
    apply_correction: bool = True,
    truth_path: Optional[str] = None,
    labels_list: Optional[List[Union[np.ndarray, pd.Series]]] = None,
    result_weights: Optional[List[float]] = None,
    slice_id: Optional[str] = None,
):
    """
    空间共识重聚类的完整流程。
    
    若提供 labels_list（多份聚类结果），则走「多结果分布 + 空间传播」Refine：
    为每个 spot 构建候选分布 P_i → 邻域平滑 → 输出建议 + 置信度 + top3 + 安全阀。
    否则使用原有「hard label 迭代投票」spatial_consensus_correction。
    
    Parameters:
    -----------
    adata : AnnData
        空间转录组数据，包含原始聚类结果
    region_key : str
        选中区域在obs中的键名
    k_neighbors : int
        邻居数量，默认8
    min_valid_neighbors : int
        最小有效邻居数（仅 hard-label 模式使用），默认3
    primary_threshold, secondary_threshold, max_voting_level, current_label_low_threshold
        仅 hard-label 模式使用
    domain_key : str
        聚类标签在obs中的键名
    spatial_key : str
        空间坐标在obsm中的键名
    consider_extended_neighbors : bool
    extended_neighbor_weight : float
    apply_correction : bool
        是否应用校正结果
    truth_path : optional
    labels_list : List[array-like], optional
        多份聚类结果（每份与 adata.obs 顺序一致）；若提供则用分布 Refine
    result_weights : List[float], optional
        多份结果的权重（分布 Refine 时使用）
        
    Returns:
    --------
    AnnData
        更新后的AnnData对象，包含重聚类结果
    """
    print("DEBUG branch:", "refine" if (labels_list is not None and len(labels_list) >= 1) else "hard", flush=True)
    print("DEBUG labels_list:", None if labels_list is None else len(labels_list), flush=True)
    print("\n" + "="*60)
    print("Spatial Consensus Correction Reclustering")
    print("基于空间共识的重聚类分析")
    print("="*60 + "\n")
    
    # 0. 选中区域 mask（用于后续 truth 校验）
    region_mask = adata.obs[region_key].astype(bool)
    region_mask_values = region_mask.values if hasattr(region_mask, 'values') else region_mask
    
    # 若提供 ground truth，先计算优化前准确率（与 Admixture 相同映射：WM=1, Layer_6=2, …, Layer_1=7）
    _load_truth_labels_fn, _accuracy_vs_truth_fn = _get_truth_accuracy_helpers()
    truth_map = _load_truth_labels_fn(truth_path) if truth_path else None
    if truth_map is not None and domain_key in adata.obs.columns:
        print("\n--- Ground Truth 校验 ---")
        _accuracy_vs_truth_fn(
            adata, region_mask_values, truth_map,
            adata.obs[domain_key].astype(str),
            "优化前 (domain)",
        )
    
    # 1. 执行校正：多结果分布 Refine 或 hard-label 迭代
    if labels_list is not None and len(labels_list) >= 1:
        print("使用多结果分布 Refine（候选分布 + 空间传播）")
        correction_results, statistics = refine_by_multi_result_distribution(
            adata,
            labels_list=labels_list,
            region_key=region_key,
            spatial_key=spatial_key,
            k_neighbors=k_neighbors,
            result_weights=result_weights,
            consider_extended_neighbors=consider_extended_neighbors,
            out_region_weight=extended_neighbor_weight,
            domain_key=domain_key,
        )
    else:
        print("使用 hard-label 迭代空间共识校正")
        correction_results, statistics = spatial_consensus_correction(
            adata,
            region_key=region_key,
            k_neighbors=k_neighbors,
            min_valid_neighbors=min_valid_neighbors,
            primary_threshold=primary_threshold,
            secondary_threshold=secondary_threshold,
            max_voting_level=max_voting_level,
            current_label_low_threshold=current_label_low_threshold,
            domain_key=domain_key,
            spatial_key=spatial_key,
            consider_extended_neighbors=consider_extended_neighbors,
            extended_neighbor_weight=extended_neighbor_weight,
        )
    
    # 2. 获取选中区域 mask（与前面一致）
    region_mask = adata.obs[region_key].astype(bool).values
    
    # 2.5 生成重聚类预览图片（ground truth / 当前结果 / 修正后结果）
    if slice_id is not None:
        generate_recluster_preview_images(
            adata,
            selected_mask=region_mask,
            correction_results=correction_results,
            slice_id=slice_id,
            truth_map=truth_map,
            domain_key=domain_key,
            spatial_key=spatial_key,
        )
    
    # 3. 整合结果
    adata = integrate_correction_results(
        adata,
        correction_results,
        region_mask,
        apply_correction=apply_correction
    )
    
    # 3.5 若提供了 ground truth，打印按优化结果全部修改后的准确率（同一映射）
    if truth_map is not None and "recluster_result" in adata.obs.columns:
        print("\n--- Ground Truth 校验（整合后）---")
        _accuracy_vs_truth_fn(
            adata, region_mask, truth_map,
            adata.obs["recluster_result"].astype(str),
            "优化后 (recluster_result)",
        )
    
    # 4. 创建概率矩阵
    n_selected = len(correction_results)
    prob_matrix, label_list = _create_probability_matrix(correction_results, n_selected)
    
    # 5. 存储额外信息到uns
    adata.uns['spatial_consensus_results'] = {
        'statistics': statistics,
        'correction_results': correction_results,
        'prob_matrix': prob_matrix,
        'label_list': label_list,
        'parameters': {
            'k_neighbors': k_neighbors,
            'min_valid_neighbors': min_valid_neighbors,
            'primary_threshold': primary_threshold,
            'secondary_threshold': secondary_threshold,
            'max_voting_level': max_voting_level
        }
    }
    
    # 6. 为每个标签创建概率列
    selected_indices = np.where(region_mask)[0]
    for i, label in enumerate(label_list):
        col_name = f'recluster_component_{i}_prob'
        adata.obs[col_name] = 0.0
        for j, idx in enumerate(selected_indices):
            if j < prob_matrix.shape[0]:
                adata.obs.loc[adata.obs.index[idx], col_name] = prob_matrix[j, i]
    
    print("\n" + "="*60)
    print("空间共识重聚类完成")
    print("="*60 + "\n")
    
    return adata


def apply_spatial_consensus_recluster(
    adata,
    selected_spots_mask,
    k_neighbors: int = 8,
    min_valid_neighbors: int = 3,
    primary_threshold: float = 0.6,
    secondary_threshold: float = 0.3,
    labels_list=None,
    result_weights=None,
    truth_path=None,
    slice_id: Optional[str] = None,
):
    adata.obs['selected_region'] = selected_spots_mask

    adata_updated = spatial_consensus_reclustering(
        adata,
        region_key='selected_region',
        k_neighbors=k_neighbors,
        min_valid_neighbors=min_valid_neighbors,
        primary_threshold=primary_threshold,
        secondary_threshold=secondary_threshold,
        max_voting_level=3,
        consider_extended_neighbors=True,
        apply_correction=True,
        labels_list=labels_list,
        result_weights=result_weights,
        truth_path=truth_path,
        slice_id=slice_id,
    )
    return adata_updated


def convert_consensus_results_to_format(adata, correction_results: List[Dict]) -> Dict:
    """
    将空间共识校正结果转换为与原接口兼容的格式
    
    Parameters:
    -----------
    adata : AnnData
        AnnData对象
    correction_results : List[Dict]
        校正结果列表
        
    Returns:
    --------
    Dict
        兼容格式的结果字典
    """
    # 收集所有标签
    all_labels = set()
    for result in correction_results:
        all_labels.add(str(result['original_label']))
        all_labels.add(str(result['suggested_label']))
    
    all_labels = sorted(list(all_labels))
    
    # 创建cluster_results格式
    hard_labels = np.array([r['suggested_label'] for r in correction_results])
    n_components = len(all_labels)
    
    # 创建简单的Q矩阵
    q_matrix = np.zeros((len(correction_results), n_components))
    label_to_idx = {label: idx for idx, label in enumerate(all_labels)}
    
    for i, result in enumerate(correction_results):
        label = str(result['suggested_label'])
        if label in label_to_idx:
            q_matrix[i, label_to_idx[label]] = result['confidence'] if result['confidence'] > 0 else 1.0
        # 归一化
        row_sum = q_matrix[i].sum()
        if row_sum > 0:
            q_matrix[i] /= row_sum
    
    cluster_results = {
        'hard_labels': hard_labels,
        'q_matrix': q_matrix,
        'n_components': n_components,
        'label_mapping': {i: label for i, label in enumerate(all_labels)}
    }
    
    return cluster_results


# ============================================================
# 测试和演示代码
# ============================================================

def test_spatial_consensus_reclustering():
    """
    测试空间共识重聚类功能
    """
    print("=== 测试空间共识重聚类功能 ===\n")
    
    # 创建模拟数据
    np.random.seed(42)
    n_cells = 500
    
    # 模拟空间坐标
    x = np.random.uniform(0, 100, n_cells)
    y = np.random.uniform(0, 100, n_cells)
    coordinates = np.column_stack([x, y])
    
    # 模拟基因表达（简单示例）
    n_genes = 100
    expression = np.random.randn(n_cells, n_genes)
    
    # 模拟聚类标签（基于空间位置）
    labels = np.zeros(n_cells, dtype=int)
    for i in range(n_cells):
        if x[i] < 33:
            labels[i] = 0
        elif x[i] < 66:
            labels[i] = 1
        else:
            labels[i] = 2
    
    # 添加一些"噪声"标签来模拟需要校正的细胞
    noise_indices = np.random.choice(n_cells, size=50, replace=False)
    labels[noise_indices] = np.random.randint(0, 3, size=50)
    
    # 创建AnnData对象
    import anndata
    adata = anndata.AnnData(X=expression)
    adata.obsm['spatial'] = coordinates
    adata.obs['domain'] = labels.astype(str)
    
    # 选择一个区域进行重聚类
    selected_mask = (x > 20) & (x < 80) & (y > 20) & (y < 80)
    
    # 模拟多份结果（多 run / 多方法），用于多结果分布 Refine
    labels_run1 = labels.astype(str)
    labels_run2 = labels.astype(str).copy()
    flip2 = np.random.choice(n_cells, size=min(30, n_cells // 10), replace=False)
    labels_run2[flip2] = np.array(["0", "1", "2"])[np.random.randint(0, 3, size=len(flip2))]
    labels_run3 = labels.astype(str).copy()
    flip3 = np.random.choice(n_cells, size=min(30, n_cells // 10), replace=False)
    labels_run3[flip3] = np.array(["0", "1", "2"])[np.random.randint(0, 3, size=len(flip3))]
    
    print(f"1. 创建模拟数据: {n_cells} 个细胞")
    print(f"2. 选中区域包含 {selected_mask.sum()} 个细胞")
    
    # 运行空间共识重聚类（传入多份结果走分布 Refine）
    print("\n3. 运行空间共识重聚类...")
    try:
        adata_updated = apply_spatial_consensus_recluster(
            adata,
            selected_mask,
            k_neighbors=8,
            min_valid_neighbors=3,
            primary_threshold=0.6,
            secondary_threshold=0.3,
            labels_list=[labels_run1, labels_run2, labels_run3],
            result_weights=[1.0, 1.0, 1.0],
            truth_path=None,
        )
        
        print("\n4. 结果统计:")
        print(f"   - 标签改变的细胞数: {adata_updated.obs['label_changed'].sum()}")
        print(f"   - 校正置信度均值: {adata_updated.obs['recluster_confidence'].mean():.3f}")
        
        if 'spatial_consensus_results' in adata_updated.uns:
            stats = adata_updated.uns['spatial_consensus_results']['statistics']
            print(f"   - 校正率: {stats['correction_rate']*100:.1f}%")
            print(f"   - 投票级别分布: {stats['voting_level_distribution']}")
        
        print("\n   ✅ 测试通过!")
        return adata_updated
        
    except Exception as e:
        print(f"\n   ❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    test_spatial_consensus_reclustering()
