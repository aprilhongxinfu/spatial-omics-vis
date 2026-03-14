"""
在独立进程中运行 UMAP，避免 Numba TBB 在非主线程 fork 时卡死。
必须在导入 numba/scanpy 之前设置 NUMBA_THREADING_LAYER。
用法: NUMBA_THREADING_LAYER=workqueue python umap_worker.py <input.npz> <output.npz>
"""
from __future__ import annotations

import os
import sys

# 必须在任何使用 Numba 的库之前设置，子进程由 main 传入 env 覆盖为 workqueue
os.environ["NUMBA_THREADING_LAYER"] = os.environ.get("NUMBA_THREADING_LAYER", "workqueue")

import numpy as np


def main():
    if len(sys.argv) != 3:
        print("Usage: umap_worker.py <input.npz> <output.npz>", file=sys.stderr)
        sys.exit(1)
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    print("UMAP 子进程: 加载数据...", flush=True)
    data = np.load(input_path, allow_pickle=True)
    emb = data["emb"]
    barcodes = data["barcodes"].tolist()
    data.close()
    n = emb.shape[0]
    print(f"UMAP 子进程: 开始计算 (n={n}，约 30 秒–2 分钟)...", flush=True)

    import scanpy as sc

    adata = sc.AnnData(X=np.zeros((n, 1)))
    adata.obs_names = barcodes
    adata.obsm["emb"] = emb.astype(np.float32)
    sc.pp.neighbors(adata, use_rep="emb", n_neighbors=15, n_pcs=None)
    # maxiter 减小以加快速度（默认约 500，200 通常足够且明显更快）
    sc.tl.umap(adata, min_dist=0.5, spread=1.0, maxiter=200)
    X_umap = adata.obsm["X_umap"]
    np.savez_compressed(output_path, X_umap=X_umap)
    print("UMAP 子进程: 完成.", flush=True)


if __name__ == "__main__":
    main()
