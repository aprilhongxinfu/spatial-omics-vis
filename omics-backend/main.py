import os
import subprocess
import sys
import tempfile
import time
os.environ["NUMBA_THREADING_LAYER"] = "tbb"  # Thread-safe alternative
print("NUMBA_THREADING_LAYER:", os.environ.get("NUMBA_THREADING_LAYER"))
import warnings

import torch
torch.set_float32_matmul_precision("medium")
from r_env import configure_r_home
configure_r_home()
print("R_HOME:", os.environ.get("R_HOME"))
print("PATH:", os.environ.get("PATH"))
print("LD_LIBRARY_PATH:", os.environ.get("LD_LIBRARY_PATH"))
print("DYLD_FALLBACK_LIBRARY_PATH:", os.environ.get("DYLD_FALLBACK_LIBRARY_PATH"))

# os.environ["NUMBA_THREADING_LAYER"] = "workqueue"  # Not thread-safe, causes concurrent access issues

# NOTE: previously this repo hard-coded a Linux-only R_HOME, which breaks on macOS.
# R_HOME is now auto-configured via `r_env.configure_r_home()`.

# Suppress common warnings to reduce noise in logs
warnings.filterwarnings("ignore", message=".*already log-transformed.*")
warnings.filterwarnings("ignore", category=UserWarning, message=".*ImplicitModificationWarning.*")
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import squidpy as sq
import scanpy as sc
import pandas as pd
import json
warnings.filterwarnings('ignore')
import SpaGCN as spg
import SEDR
from sqlalchemy import Table, Column, Integer, String, MetaData, TIMESTAMP, Float, func, create_engine, insert, text,Text,select, UniqueConstraint, PrimaryKeyConstraint, Index, Index
from sqlalchemy import insert, update, select, func, text
from sqlalchemy.exc import ProgrammingError
try:
    from rpy2.robjects import pandas2ri
    from rpy2.robjects import conversion, pandas2ri
    import rpy2.robjects as robjects
    import rpy2.robjects.numpy2ri
    from rpy2.robjects.conversion import localconverter
    RPY2_AVAILABLE = True
except Exception as _e:
    # Allow the API to boot even if R/rpy2 is not correctly configured.
    RPY2_AVAILABLE = False
    pandas2ri = None
    conversion = None
    robjects = None
    localconverter = None
import re
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import GridSearchCV
from sklearn.preprocessing import StandardScaler
import numpy as np
import gseapy as gp
from typing import List, Dict, Any
from dotenv import load_dotenv
import threading
# from GraphST.utils import clustering
from GraphST import GraphST
from utils import *
# Thread lock for protecting global adata access
adata_lock = threading.Lock()
from sqlalchemy.dialects.mysql import insert as mysql_insert
from scipy.spatial import Delaunay
from scipy.stats import zscore
from scipy.optimize import linear_sum_assignment
import numpy as np
import pandas as pd
from sklearn import metrics
from sklearn.metrics import silhouette_score, calinski_harabasz_score, silhouette_samples
from sklearn.neighbors import kneighbors_graph, NearestNeighbors
import scanpy as sc
import ot
from sklearn.decomposition import PCA
# rpy2 imports handled above (RPY2_AVAILABLE)
from dateutil.parser import parse
# from HVG_explain import interpret_enrichment_with_llm  # Commented out: AI interpretation disabled
from hvg_enrichment import perform_hvg_enrichment, perform_hvg_enrichment_by_cluster
from cellchat import perform_cellchat_analysis
from llm_analysis import analyze_downstream_results  # LLM analysis (OpenAI-compatible)
from flask import Flask, jsonify
from pathlib import Path
from fastapi import FastAPI, UploadFile, Form
from typing import Annotated, Optional
from datetime import datetime
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from scipy.spatial import Delaunay
import numpy as np
import pandas as pd
import math



load_dotenv()
import random
user = os.getenv("DB_USER")
password = os.getenv("DB_PASSWORD")
host = os.getenv("DB_HOST")
db = os.getenv("DB_NAME")
def sanitize_float_for_json(value):
    """
    Sanitize float values for JSON serialization by replacing NaN and infinity with None.

    Parameters:
    -----------
    value : float or None
        The value to sanitize

    Returns:
    --------
    float or None
        The sanitized value, or None if the original value was NaN or infinity
    """
    if value is None:
        return None
    try:
        # Check for NaN
        if np.isnan(value):
            return None
        # Check for infinity
        if np.isinf(value):
            return None
        # Check for very large values that might cause issues
        if abs(value) > 1e308:  # Close to float64 max
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def set_all_seeds(seed=2020):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)


# 建立连接
# engine = create_engine("sqlite:///omics_data.db", echo=True) 
engine = create_engine("sqlite:///omics_data.db", connect_args={"check_same_thread": False})
# engine = create_engine("mysql+pymysql://root:123456@10.7.138.121/omics_data", echo=True)   #连接本地数据库 shiyanhi
# engine = create_engine("mysql+pymysql://root:123456@10.4.138.39/omics_data", echo=True)   #连接本地数据库

metadata = MetaData()
loaded_slice_id = None

# pandas2ri.activate()


def insert_initial_clusters(adata, engine, slice_id):
    metadata = MetaData()
    metadata.reflect(bind=engine)
    table_name = f"spot_cluster_{slice_id}"
    spot_cluster = metadata.tables[table_name]

    with engine.connect() as conn:
        result = conn.execute(spot_cluster.select().limit(1)).fetchone()
        if result is not None:
            print(f"Table {table_name} already has data. Skipping insertion.")
            return

        # ✅ 构造插入列表
        records = []
        for i, (barcode, row) in enumerate(adata.obs.iterrows()):
            x, y = map(float, adata.obsm["spatial"][i])
            emb_vec = adata.obsm["emb"][i]
            emb_str = ",".join(map(str, emb_vec))  # 将嵌入向量转为字符串

            records.append({
                "barcode": barcode,
                "cluster_result_id": "default",
                "cluster": str(row["domain"]),
                "x": x,
                "y": y,
                "n_count_spatial": float(row.get("nCount_Spatial", None)),
                "n_feature_spatial": float(row.get("nFeature_Spatial", None)),
                "percent_mito": float(row.get("pct_counts_mt", None)),
                "percent_ribo": float(row.get("pct_counts_ribo", None)),
                "emb": emb_str,
            })

        # ✅ 批量插入
        for record in records:
            conn.execute(insert(spot_cluster).values(record))
        conn.commit()
        print(f"✅ 批量插入 {len(records)} 条记录到 {table_name}")

        
def migrate_cluster_method_table():
    """
    迁移 cluster_method 表：从单主键改为复合主键
    """
    try:
        with engine.connect() as conn:
            # 检查表是否存在
            result = conn.execute(text("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='cluster_method'
            """)).fetchone()
            
            if result:
                # 检查是否已经有 cluster_result_id 列
                result_cols = conn.execute(text("PRAGMA table_info(cluster_method)")).fetchall()
                has_cluster_result_id = any(col[1] == "cluster_result_id" for col in result_cols)
                has_result_name = any(col[1] == "result_name" for col in result_cols)
                
                if not has_cluster_result_id:
                    print("🔄 迁移 cluster_method 表：添加 cluster_result_id 列...")
                    # 添加 cluster_result_id 列，默认值为 "default"
                    conn.execute(text("ALTER TABLE cluster_method ADD COLUMN cluster_result_id VARCHAR(100) DEFAULT 'default'"))
                    conn.commit()
                
                if not has_result_name:
                    print("🔄 迁移 cluster_method 表：添加 result_name 列...")
                    conn.execute(text("ALTER TABLE cluster_method ADD COLUMN result_name VARCHAR(200)"))
                    conn.commit()
                
                has_plot_path = any(col[1] == "plot_path" for col in result_cols)
                if not has_plot_path:
                    print("🔄 迁移 cluster_method 表：添加 plot_path 列...")
                    conn.execute(text("ALTER TABLE cluster_method ADD COLUMN plot_path VARCHAR(500)"))
                    conn.commit()
                
                # 添加聚类评估指标列
                has_chao = any(col[1] == "chao" for col in result_cols)
                if not has_chao:
                    print("🔄 迁移 cluster_method 表：添加评估指标列...")
                    conn.execute(text("ALTER TABLE cluster_method ADD COLUMN chao FLOAT"))
                    conn.execute(text("ALTER TABLE cluster_method ADD COLUMN silhouette FLOAT"))
                    conn.execute(text("ALTER TABLE cluster_method ADD COLUMN pas FLOAT"))
                    conn.execute(text("ALTER TABLE cluster_method ADD COLUMN morans_i FLOAT"))
                    conn.commit()
                
                # 检查主键约束
                pk_info = conn.execute(text("PRAGMA table_info(cluster_method)")).fetchall()
                # 查找主键列
                pk_columns = [col[1] for col in pk_info if col[5] == 1]  # col[5] 是 pk 标志
                
                # 如果 slice_id 是主键，需要删除旧主键并创建复合主键
                if "slice_id" in pk_columns and "cluster_result_id" not in pk_columns:
                    print("🔄 迁移 cluster_method 表：修改主键约束...")
                    # SQLite 不支持直接修改主键，需要重建表
                    # 检查是否有指标字段
                    has_plot_path = any(col[1] == "plot_path" for col in result_cols)
                    has_chao = any(col[1] == "chao" for col in result_cols)
                    has_silhouette = any(col[1] == "silhouette" for col in result_cols)
                    has_pas = any(col[1] == "pas" for col in result_cols)
                    has_morans_i = any(col[1] == "morans_i" for col in result_cols)
                    
                    # 1. 创建新表（包含所有字段，包括指标）
                    create_table_sql = """
                        CREATE TABLE cluster_method_new (
                            slice_id VARCHAR(50) NOT NULL,
                            cluster_result_id VARCHAR(100) NOT NULL DEFAULT 'default',
                            result_name VARCHAR(200),
                            method VARCHAR(50) NOT NULL,
                            n_clusters INTEGER,
                            epoch INTEGER,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"""
                    
                    if has_plot_path:
                        create_table_sql += ",\n                            plot_path VARCHAR(500)"
                    if has_chao:
                        create_table_sql += ",\n                            chao FLOAT"
                    if has_silhouette:
                        create_table_sql += ",\n                            silhouette FLOAT"
                    if has_pas:
                        create_table_sql += ",\n                            pas FLOAT"
                    if has_morans_i:
                        create_table_sql += ",\n                            morans_i FLOAT"
                    
                    create_table_sql += ",\n                            PRIMARY KEY (slice_id, cluster_result_id)\n                        )"
                    
                    conn.execute(text(create_table_sql))
                    
                    # 2. 复制数据（包括所有字段，特别是指标字段）
                    select_fields = ["slice_id", "COALESCE(cluster_result_id, 'default') as cluster_result_id"]
                    insert_fields = ["slice_id", "cluster_result_id"]
                    
                    # 添加可选字段
                    for field in ["result_name", "method", "n_clusters", "epoch", "updated_at"]:
                        if any(col[1] == field for col in result_cols):
                            select_fields.append(field)
                            insert_fields.append(field)
                    
                    # 添加指标字段
                    if has_plot_path:
                        select_fields.append("plot_path")
                        insert_fields.append("plot_path")
                    if has_chao:
                        select_fields.append("chao")
                        insert_fields.append("chao")
                    if has_silhouette:
                        select_fields.append("silhouette")
                        insert_fields.append("silhouette")
                    if has_pas:
                        select_fields.append("pas")
                        insert_fields.append("pas")
                    if has_morans_i:
                        select_fields.append("morans_i")
                        insert_fields.append("morans_i")
                    
                    copy_sql = f"""
                        INSERT INTO cluster_method_new 
                        ({', '.join(insert_fields)})
                        SELECT 
                            {', '.join(select_fields)}
                        FROM cluster_method
                    """
                    
                    conn.execute(text(copy_sql))
                    
                    # 3. 删除旧表
                    conn.execute(text("DROP TABLE cluster_method"))
                    
                    # 4. 重命名新表
                    conn.execute(text("ALTER TABLE cluster_method_new RENAME TO cluster_method"))
                    conn.commit()
                    print("✅ cluster_method 表迁移完成（已保留所有指标字段）")
    except Exception as e:
        print(f"⚠️ 迁移 cluster_method 表时出错: {e}")
        # 如果迁移失败，继续执行，让 create_all 处理


def create_tables(slice_id):
    # 先尝试迁移 cluster_method 表
    migrate_cluster_method_table()
    
    table_name = f"spot_cluster_{slice_id}"
    spot_cluster = Table(
        table_name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("barcode", String(50), nullable=False),
        Column("cluster_result_id", String(100), nullable=False, server_default="default"),
        Column("cluster", String(50), nullable=False),
        Column("x", Float),
        Column("y", Float),
        Column("n_count_spatial", Float),
        Column("n_feature_spatial", Float),
        Column("percent_mito", Float),
        Column("percent_ribo", Float),
        Column("emb",Text),
        Column("updated_at", TIMESTAMP, server_default=func.now(), onupdate=func.now()),
        # 添加复合唯一约束：同一个barcode在同一个cluster_result_id下只能有一条记录
        UniqueConstraint("barcode", "cluster_result_id", name=f"uq_{table_name}_barcode_result"),
        extend_existing=True
    )

    cluster_log = Table(
        "cluster_log",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("slice_id", String(50), nullable=False),
        Column("cluster_result_id", String(100), nullable=False, server_default="default"),
        Column("barcode", String(50), nullable=False),
        Column("old_cluster", String(50), nullable=False),
        Column("new_cluster", String(50), nullable=False),
        Column("comment", String(255)),
        Column("updated_at", TIMESTAMP, server_default=func.now(), onupdate=func.now()),
        # 添加索引以优化基于cluster_result_id和barcode的查询性能
        Index("idx_cluster_log_slice_result", "slice_id", "cluster_result_id"),
        Index("idx_cluster_log_slice_result_barcode", "slice_id", "cluster_result_id", "barcode"),
        extend_existing=True
    )
    
    # 使用 autoload_with 来加载现有表，避免重新定义冲突
    try:
        metadata.reflect(bind=engine, only=["cluster_method"])
        cluster_method = metadata.tables["cluster_method"]
        # 如果表已存在，检查是否需要添加列
        with engine.connect() as conn:
            result_cols = conn.execute(text("PRAGMA table_info(cluster_method)")).fetchall()
            existing_cols = [col[1] for col in result_cols]
            if "cluster_result_id" not in existing_cols:
                conn.execute(text("ALTER TABLE cluster_method ADD COLUMN cluster_result_id VARCHAR(100) DEFAULT 'default'"))
            if "result_name" not in existing_cols:
                conn.execute(text("ALTER TABLE cluster_method ADD COLUMN result_name VARCHAR(200)"))
            if "plot_path" not in existing_cols:
                conn.execute(text("ALTER TABLE cluster_method ADD COLUMN plot_path VARCHAR(500)"))
            # 添加聚类评估指标列
            if "chao" not in existing_cols:
                conn.execute(text("ALTER TABLE cluster_method ADD COLUMN chao FLOAT"))
            if "silhouette" not in existing_cols:
                conn.execute(text("ALTER TABLE cluster_method ADD COLUMN silhouette FLOAT"))
            if "pas" not in existing_cols:
                conn.execute(text("ALTER TABLE cluster_method ADD COLUMN pas FLOAT"))
            if "morans_i" not in existing_cols:
                conn.execute(text("ALTER TABLE cluster_method ADD COLUMN morans_i FLOAT"))
            conn.commit()
    except Exception:
        # 如果表不存在或加载失败，创建新表
        cluster_method = Table(
            "cluster_method",
            metadata,
            Column("slice_id", String(50), nullable=False),
            Column("cluster_result_id", String(100), nullable=False, server_default="default"),
            Column("result_name", String(200)),  # 可选的聚类结果名称，便于用户识别
            Column("method", String(50), nullable=False),
            Column("n_clusters", Integer),
            Column("epoch", Integer),
            Column("plot_path", String(500)),  # 保存的 plot 图片路径
            Column("chao", Float),  # CHAO (Calinski-Harabasz) 指数
            Column("silhouette", Float),  # 轮廓系数
            Column("pas", Float),  # PAS 指标
            Column("morans_i", Float),  # Moran's I 空间自相关指数
            Column("updated_at", TIMESTAMP, server_default=func.now(), onupdate=func.now()),
            # 复合主键：同一个切片可以有多个聚类结果
            PrimaryKeyConstraint("slice_id", "cluster_result_id", name="pk_cluster_method"),
            extend_existing=True
        )

    # 创建簇级指标表
    try:
        metadata.reflect(bind=engine, only=["cluster_metrics"])
        cluster_metrics_table = metadata.tables["cluster_metrics"]
        # 如果表已存在，检查是否需要添加列
        with engine.connect() as conn:
            result_cols = conn.execute(text("PRAGMA table_info(cluster_metrics)")).fetchall()
            existing_cols = [col[1] for col in result_cols]
            if "gearys_c" not in existing_cols:
                conn.execute(text("ALTER TABLE cluster_metrics ADD COLUMN gearys_c FLOAT"))
            conn.commit()
    except Exception:
        # 如果表不存在或加载失败，创建新表
        cluster_metrics_table = Table(
            "cluster_metrics",
            metadata,
            Column("slice_id", String(50), nullable=False),
            Column("cluster_result_id", String(100), nullable=False, server_default="default"),
            Column("cluster", String(50), nullable=False),
            Column("size", Integer),
            Column("silhouette", Float),
            Column("morans_i", Float),
            Column("gearys_c", Float),
            Column("updated_at", TIMESTAMP, server_default=func.now(), onupdate=func.now()),
            # 复合主键：同一个聚类结果的同一个簇只能有一条记录
            PrimaryKeyConstraint("slice_id", "cluster_result_id", "cluster", name="pk_cluster_metrics"),
            extend_existing=True
        )

    try:
        metadata.create_all(engine)
    except ProgrammingError as e:
        print(f"Error creating tables: {e}")
    
    # 为 cluster_log 表创建索引以优化查询性能
    try:
        with engine.connect() as conn:
            # 检查索引是否已存在
            index_info = conn.execute(text("""
                SELECT name FROM sqlite_master 
                WHERE type='index' AND tbl_name='cluster_log'
            """)).fetchall()
            existing_indexes = [row[0] for row in index_info]
            
            # 创建复合索引 (slice_id, cluster_result_id) 用于按聚类结果查询日志
            if "idx_cluster_log_slice_result" not in existing_indexes:
                conn.execute(text("""
                    CREATE INDEX idx_cluster_log_slice_result 
                    ON cluster_log(slice_id, cluster_result_id)
                """))
                print("✅ 创建索引: idx_cluster_log_slice_result")
            
            # 创建复合索引 (cluster_result_id, barcode) 用于按spot查询日志
            if "idx_cluster_log_result_barcode" not in existing_indexes:
                conn.execute(text("""
                    CREATE INDEX idx_cluster_log_result_barcode 
                    ON cluster_log(cluster_result_id, barcode)
                """))
                print("✅ 创建索引: idx_cluster_log_result_barcode")
            
            # 创建复合索引 (slice_id, cluster_result_id, barcode) 用于精确查询
            if "idx_cluster_log_slice_result_barcode" not in existing_indexes:
                conn.execute(text("""
                    CREATE INDEX idx_cluster_log_slice_result_barcode 
                    ON cluster_log(slice_id, cluster_result_id, barcode)
                """))
                print("✅ 创建索引: idx_cluster_log_slice_result_barcode")
            
            conn.commit()
    except Exception as e:
        print(f"⚠️ 创建 cluster_log 索引时出错: {e}")


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# app.mount("/images", StaticFiles(directory="./data/151673/spatial"), name="images")

# 全局路径与缓存
slice_id = ""
path = ""
scale ="hires"
spatial_dir = ""
sf = None
scale_key = "" 
factor = None

adata = None
expression_data = None


@app.get("/images/{slice_id}/tissue_hires_image.png")
def get_image(slice_id: str):
    spatial_dir = f"./data/{slice_id}/spatial"
    path = os.path.join(spatial_dir, "tissue_hires_image.png")
    if not os.path.exists(path):
        path = os.path.join(spatial_dir, "tissue_lowres_image.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type="image/png")

@app.get("/images/{slice_id}/plots/{plot_filename}")
def get_cluster_plot(slice_id: str, plot_filename: str):
    """
    获取保存的聚类结果 plot 图片
    """
    path = f"./data/{slice_id}/plots/{plot_filename}"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Plot image not found")
    return FileResponse(path, media_type="image/png")


def _set_mito_ribo_genes(adata):
    """
    为 adata.var 设置 mt/ribo 标记，支持多种基因命名（人/鼠/其它常见格式），
    避免因命名不匹配导致 percent_mito/percent_ribo 全为 0。
    若矩阵中本身无核糖体/线粒体基因（如已过滤或为子集），对应比例会为 0 属正常。
    """
    names = adata.var_names.astype(str).str.strip()
    # 线粒体：人 MT-、鼠 mt-/Mt-、以及 MT. / MTRNR / MTND / MTCO / MTATP 等
    mt = (
        names.str.startswith("MT-")
        | names.str.startswith("mt-")
        | names.str.startswith("Mt-")
        | names.str.startswith("MT.")
        | names.str.match(r"^MT[A-Z0-9]", case=False, na=False)
    )
    adata.var["mt"] = mt
    # 核糖体：RPS/RPL（人）、Rps/Rpl（鼠）、全小写 rps/rpl、线粒体核糖体 MRPS/MRPL/Mrps/Mrpl，
    # 以及 ^RP 后跟 S/L 的任意变体（含数字后缀如 RPS1、Rpl7a）
    ribo = (
        names.str.startswith(("RPS", "RPL", "Rps", "Rpl", "rps", "rpl"))
        | names.str.startswith(("MRPS", "MRPL", "Mrps", "Mrpl", "mrps", "mrpl"))
        | names.str.match(r"^RP[SL]\w*", case=False, na=False)
        | names.str.match(r"^Rp[sl]\w*", na=False)
    )
    adata.var["ribo"] = ribo


def _load_visium_from_matrix_csv(base_path: str):
    """
    当 filtered_feature_bc_matrix.h5 不存在时，从 matrix_counts.csv 与 tissue_positions 构建 AnnData。
    - matrix_counts.csv: 第一列为基因名，表头为 barcode（spots），值为 counts；可在 base_path/spatial/ 或 base_path/ 下。
    - tissue_positions_list.csv: 无表头，列为 barcode, in_tissue, array_row, array_col, x, y；位于 base_path/spatial/。
    """
    base_path = os.path.normpath(base_path)
    spatial_dir = os.path.join(base_path, "spatial")

    # 查找 matrix_counts.csv（优先 spatial 下）
    matrix_path = os.path.join(spatial_dir, "matrix_counts.csv")
    if not os.path.isfile(matrix_path):
        matrix_path = os.path.join(base_path, "matrix_counts.csv")
    if not os.path.isfile(matrix_path):
        raise FileNotFoundError(f"matrix_counts.csv 不存在于 {spatial_dir} 或 {base_path}")

    # 读取表达矩阵：CSV 为 genes x spots，AnnData 需要 obs x var，故转置
    print(f"📂 从 CSV 加载表达矩阵: {matrix_path}")
    counts_df = pd.read_csv(matrix_path, index_col=0)
    if counts_df.shape[0] == 0 or counts_df.shape[1] == 0:
        raise ValueError("matrix_counts.csv 为空或格式不正确")
    # counts_df: index = genes, columns = barcodes
    counts_df = counts_df.T
    counts_df.index = counts_df.index.astype(str).str.strip('"')
    barcodes = counts_df.index.tolist()
    genes = counts_df.columns.tolist()

    # 读取空间坐标
    tp_path = os.path.join(spatial_dir, "tissue_positions_list.csv")
    if not os.path.isfile(tp_path):
        raise FileNotFoundError(f"tissue_positions_list.csv 不存在于 {spatial_dir}")
    tp = pd.read_csv(tp_path, header=None)
    if tp.shape[1] < 6:
        raise ValueError("tissue_positions_list.csv 至少需要 6 列 (barcode, in_tissue, array_row, array_col, x, y)")
    # 10x 2.0: 第5列=pxl_row=y，第6列=pxl_col=x（与官方文档一致）
    tp.columns = ["barcode", "in_tissue", "array_row", "array_col", "col5", "col6"][: tp.shape[1]]
    tp["barcode"] = tp["barcode"].astype(str).str.strip('"')
    tp = tp.set_index("barcode")

    spatial_xy = np.zeros((len(barcodes), 2), dtype=np.float64)
    for i, bc in enumerate(barcodes):
        if bc in tp.index:
            spatial_xy[i, 0] = tp.loc[bc, "col6"]   # x = pxl_col
            spatial_xy[i, 1] = tp.loc[bc, "col5"]   # y = pxl_row
        else:
            spatial_xy[i] = np.nan

    X = np.asarray(counts_df.values, dtype=np.float32)
    adata_local = sc.AnnData(X=X, obs=pd.DataFrame(index=barcodes), var=pd.DataFrame(index=genes))
    adata_local.obsm["spatial"] = spatial_xy
    print(f"📦 从 matrix_counts.csv 加载完成, shape: {adata_local.shape}")
    return adata_local


def prepare_data(force_reload=False):
    global adata, path, loaded_slice_id, slice_id

    with adata_lock:
        print(f"🔧 准备加载数据: slice_id = {slice_id}, loaded_slice_id = {loaded_slice_id}, force_reload = {force_reload}")
        
        if not force_reload and adata is not None and slice_id == loaded_slice_id:
            print("✅ 当前切片已加载，跳过")
            return adata

        loaded_slice_id = slice_id
        path = f"./data/{slice_id}"
        print(f"📁 设置数据路径: {path}")

        create_tables(slice_id)

        h5_path = os.path.join(path, "filtered_feature_bc_matrix.h5")
        matrix_csv_path = os.path.join(path, "spatial", "matrix_counts.csv")
        if not os.path.isfile(matrix_csv_path):
            matrix_csv_path = os.path.join(path, "matrix_counts.csv")

        if os.path.isfile(h5_path):
            adata_local = sq.read.visium(path=path)
            print(f"📦 Visium 数据加载完成 (h5), shape: {adata_local.shape}")
        elif os.path.isfile(matrix_csv_path):
            adata_local = _load_visium_from_matrix_csv(path)
        else:
            raise FileNotFoundError(
                "未找到 filtered_feature_bc_matrix.h5 或 matrix_counts.csv，请准备该切片的 Visium 数据"
            )

        if not adata_local.var_names.is_unique:
            print("⚠️ 检测到重复基因名，正在修复")
            adata_local.var_names_make_unique()

        adata_local.obs["nCount_Spatial"] = (
            adata_local.X.sum(axis=1).A1 if hasattr(adata_local.X, "A1") else adata_local.X.sum(axis=1)
        )
        adata_local.obs["nFeature_Spatial"] = (
            (adata_local.X > 0).sum(1).A1 if hasattr(adata_local.X, "A1") else (adata_local.X > 0).sum(1)
        )

        _set_mito_ribo_genes(adata_local)
        sc.pp.calculate_qc_metrics(adata_local, qc_vars=["mt", "ribo"], inplace=True)

        if not (adata_local.X.min() >= 0 and adata_local.X.max() <= 20):
            print("📉 数据未 log 转换，执行 normalize + log1p")
            sc.pp.normalize_total(adata_local)
            sc.pp.log1p(adata_local)
        else:
            print("⚠️ 数据可能已 log 转换，跳过")

        sc.pp.highly_variable_genes(adata_local, flavor="seurat", n_top_genes=2000)

        table_name = f"spot_cluster_{slice_id}"
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar()

            if not result or result == 0:
                print("⚠️ 数据库中无记录，初始化 cluster='unknown'")
                metadata = MetaData()
                metadata.reflect(bind=engine)
                spot_cluster = metadata.tables[table_name]

                # 安全转换：None/NaN -> 0，保证新切片的小提琴图后两项（percent_mito/percent_ribo）有值
                def _safe_float(v):
                    if v is None or (isinstance(v, float) and np.isnan(v)):
                        return 0.0
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return 0.0

                records = []
                for i, (barcode, row) in enumerate(adata_local.obs.iterrows()):
                    x, y = map(float, adata_local.obsm["spatial"][i])
                    records.append({
                        "barcode": barcode,
                        "cluster_result_id": "default",
                        "cluster": "unknown",
                        "x": x,
                        "y": y,
                        "n_count_spatial": _safe_float(row.get("nCount_Spatial")),
                        "n_feature_spatial": _safe_float(row.get("nFeature_Spatial")),
                        "percent_mito": _safe_float(row.get("pct_counts_mt")),
                        "percent_ribo": _safe_float(row.get("pct_counts_ribo")),
                        "emb": "",
                    })

                batch_size = 100
                for i in range(0, len(records), batch_size):
                    batch = records[i:i + batch_size]
                    for record in batch:
                        conn.execute(insert(spot_cluster).values(record))
                    conn.commit()

                print(f"✅ 插入 {len(records)} 条记录")
                adata_local.obs["domain"] = "unknown"
                adata = adata_local.copy()
                return adata

            print(f"✅ 数据库已有 {result} 条记录，加载聚类和 embedding 信息")
            # 默认加载 cluster_result_id = "default" 的数据
            df = pd.read_sql(text(f"SELECT * FROM `{table_name}` WHERE cluster_result_id = 'default'"), conn).set_index("barcode")

            # 检查是否需要更新 percent_mito 和 percent_ribo
            need_update_metrics = False
            if "percent_mito" in df.columns and "percent_ribo" in df.columns:
                # 检查是否有NULL值或缺失值
                if df["percent_mito"].isna().any() or df["percent_ribo"].isna().any():
                    print("⚠️ 检测到 percent_mito 或 percent_ribo 有 NULL 值，将重新计算并更新")
                    need_update_metrics = True
                # 检查是否所有值都是0.0（可能是之前计算错误导致的，比如基因命名不匹配）
                elif (df["percent_mito"].abs() < 1e-10).all() and (df["percent_ribo"].abs() < 1e-10).all():
                    print("⚠️ 检测到 percent_mito 和 percent_ribo 全为0，可能是基因命名不匹配导致的，将重新计算并更新")
                    need_update_metrics = True
            else:
                print("⚠️ 数据库缺少 percent_mito 或 percent_ribo 字段，将重新计算并更新")
                need_update_metrics = True

            # 如果指标缺失，使用已计算的值更新数据库
            if need_update_metrics:
                # 确保已经计算了QC指标（在prepare_data的前面已经计算过）
                if "pct_counts_mt" not in adata_local.obs.columns or "pct_counts_ribo" not in adata_local.obs.columns:
                    _set_mito_ribo_genes(adata_local)
                    sc.pp.calculate_qc_metrics(adata_local, qc_vars=["mt", "ribo"], inplace=True)
                
                # 更新数据库中的指标
                metadata = MetaData()
                metadata.reflect(bind=engine)
                spot_cluster = metadata.tables[table_name]
                
                # 批量更新
                for barcode in adata_local.obs_names:
                    pct_mito = float(adata_local.obs.loc[barcode, "pct_counts_mt"]) if "pct_counts_mt" in adata_local.obs.columns else None
                    pct_ribo = float(adata_local.obs.loc[barcode, "pct_counts_ribo"]) if "pct_counts_ribo" in adata_local.obs.columns else None
                    conn.execute(
                        update(spot_cluster)
                        .where(
                            spot_cluster.c.barcode == barcode,
                            spot_cluster.c.cluster_result_id == "default"
                        )
                        .values(
                            percent_mito=pct_mito,
                            percent_ribo=pct_ribo
                        )
                    )
                conn.commit()
                print("✅ 已更新数据库中的 percent_mito 和 percent_ribo")
                
                # 重新读取更新后的数据
                df = pd.read_sql(text(f"SELECT * FROM `{table_name}` WHERE cluster_result_id = 'default'"), conn).set_index("barcode")

            for col in ["cluster", "x", "y", "n_count_spatial", "n_feature_spatial", "percent_mito", "percent_ribo"]:
                if col in df.columns:
                    if col == "cluster":
                        adata_local.obs[col] = df.loc[adata_local.obs_names, col].astype(str)
                    else:
                        adata_local.obs[col] = df.loc[adata_local.obs_names, col].astype(float)

            if "emb" in df.columns:
                def safe_parse(s):
                    try:
                        vec = np.fromstring(s, sep=",")
                        return vec if len(vec) > 0 else None
                    except:
                        return None

                emb_matrix = df["emb"].apply(safe_parse)
                valid_emb = emb_matrix.dropna()
                if valid_emb.empty:
                    print("⚠️ 所有 embedding 为空，跳过 embedding 恢复")
                else:
                    embedding_dim = len(valid_emb.iloc[0])
                    print(f"📐 embedding 维度为 {embedding_dim}")

                    filled_emb = np.zeros((len(adata_local.obs_names), embedding_dim))
                    for i, barcode in enumerate(adata_local.obs_names):
                        vec = emb_matrix.get(barcode)
                        if vec is not None:
                            filled_emb[i] = vec
                    adata_local.obsm["emb"] = filled_emb
            else:
                print("❗️ 未发现 emb 字段")

        # 确保 cluster 和 domain 字段都存在
        if "cluster" in adata_local.obs.columns:
            adata_local.obs["domain"] = adata_local.obs["cluster"].astype("category")
        elif "domain" in adata_local.obs.columns:
            adata_local.obs["cluster"] = adata_local.obs["domain"].astype(str)
        else:
            # 如果都没有，设置默认值
            adata_local.obs["cluster"] = "unknown"
            adata_local.obs["domain"] = "unknown"
        
        adata = adata_local
        print("✅ 切片数据加载完成")
        return adata
    
class ClusteringRequest(BaseModel):
    slice_id: str
    n_clusters: int = 7
    method: str = "mclust"
    epoch: int = 500
    cluster_result_id: str = "default"  # 聚类结果ID，用于区分不同的聚类结果
    result_name: str = None  # 可选的聚类结果名称


def compute_clustering_metrics(adata_obj, slice_id: str, cluster_result_id: str) -> dict:
    """
    计算聚类评估指标：CHAO (Calinski-Harabasz)、轮廓系数、PAS 和 Moran's I
    返回包含这些指标的字典
    """
    try:
        if adata_obj is None:
            raise ValueError("adata 对象为空，无法计算指标。")
        
        adata_local = adata_obj.copy()
        
        # 获取聚类标签
        if "domain" not in adata_local.obs:
            raise ValueError("缺少聚类标签信息（obs['domain']），无法计算指标。")
        
        labels = adata_local.obs["domain"].astype(str)
        # 将标签转换为整数索引以便计算
        unique_labels = sorted(labels.unique())
        label_to_int = {label: i for i, label in enumerate(unique_labels)}
        labels_int = np.array([label_to_int[label] for label in labels])
        
        # 检查聚类数量
        n_clusters = len(unique_labels)
        if n_clusters < 2:
            print("⚠️ 聚类数量少于2，无法计算部分指标")
            return {
                "chao": None,
                "silhouette": None,
                "pas": None,
                "morans_i": None
            }
        
        # 获取特征矩阵（使用 embedding 或 PCA）
        if "emb" in adata_local.obsm:
            X = adata_local.obsm["emb"]
        elif "X_pca" in adata_local.obsm:
            X = adata_local.obsm["X_pca"]
        else:
            # 如果没有 embedding，使用原始表达数据的前50个PC
            adata_temp = adata_local.copy()
            if not (adata_temp.X.min() >= 0 and adata_temp.X.max() <= 20):
                sc.pp.normalize_total(adata_temp)
                sc.pp.log1p(adata_temp)
            sc.pp.pca(adata_temp, n_comps=min(50, adata_temp.n_obs - 1, adata_temp.n_vars))
            X = adata_temp.obsm["X_pca"]
        
        # 转换为 numpy array
        if hasattr(X, "toarray"):
            X = X.toarray()
        X = np.asarray(X)
        
        metrics_dict = {}
        
        # 1. 计算 CHAO (Calinski-Harabasz 指数)
        try:
            print(f"   🔢 计算 CHAO 指标...")
            chao_score = calinski_harabasz_score(X, labels_int)
            metrics_dict["chao"] = float(chao_score)
            print(f"   ✅ CHAO = {chao_score:.4f}")
        except Exception as e:
            print(f"⚠️ 计算 CHAO 指标失败: {e}")
            metrics_dict["chao"] = None
        
        # 2. 计算轮廓系数 (Silhouette Score)
        try:
            print(f"   🔢 计算轮廓系数（这可能需要一些时间）...")
            silhouette = silhouette_score(X, labels_int)
            metrics_dict["silhouette"] = float(silhouette)
            print(f"   ✅ Silhouette = {silhouette:.4f}")
        except Exception as e:
            print(f"⚠️ 计算轮廓系数失败: {e}")
            metrics_dict["silhouette"] = None
        
        # 3. 计算 PAS (Percentage of Abnormal Spots - 异常点百分比)
        try:
            if "spatial" in adata_local.obsm:
                print(f"   🔢 计算 PAS 指标...")
                pas_score = _compute_pas(labels_int, adata_local.obsm["spatial"], k=6)
                metrics_dict["pas"] = float(pas_score) if not np.isnan(pas_score) else None
                if metrics_dict["pas"] is not None:
                    print(f"   ✅ PAS = {pas_score:.4f}")
                else:
                    print(f"   ⚠️ PAS 计算结果为 NaN")
            else:
                print("⚠️ 缺少空间坐标信息，无法计算 PAS")
                metrics_dict["pas"] = None
        except Exception as e:
            print(f"⚠️ 计算 PAS 失败: {e}")
            metrics_dict["pas"] = None
        
        # 4. 计算 Moran's I (空间自相关)
        try:
            if "spatial" in adata_local.obsm:
                spatial_coords = adata_local.obsm["spatial"]
                print(f"   📊 开始计算 Moran's I（聚类数: {n_clusters}, spot数: {len(labels_int)}）...")
                morans_scores = []
                
                # 对每个聚类计算 Moran's I（限制最多计算前10个聚类，避免卡顿）
                max_clusters_for_moran = min(10, n_clusters)
                for label_idx, label in enumerate(unique_labels[:max_clusters_for_moran]):
                    # one-hot 编码
                    onehot = (labels_int == label_idx).astype(float)
                    print(f"   📊 计算聚类 {label} 的 Moran's I...")
                    moran_i = _compute_morans_i(onehot, spatial_coords, k=6)
                    if not np.isnan(moran_i):
                        morans_scores.append(moran_i)
                        print(f"   ✅ 聚类 {label} 的 Moran's I = {moran_i:.4f}")
                    else:
                        print(f"   ⚠️ 聚类 {label} 的 Moran's I 计算结果为 NaN")
                
                if morans_scores:
                    # 取平均并归一化到 0-1 范围（Moran's I 范围是 -1 到 1）
                    mean_moran = np.mean(morans_scores)
                    metrics_dict["morans_i"] = float((mean_moran + 1) / 2)  # 归一化到 0-1
                    print(f"   ✅ Moran's I 计算完成: 平均 = {mean_moran:.4f}, 归一化 = {metrics_dict['morans_i']:.4f}")
                else:
                    print("   ⚠️ 所有聚类的 Moran's I 计算失败")
                    metrics_dict["morans_i"] = None
            else:
                print("⚠️ 缺少空间坐标信息，无法计算 Moran's I")
                metrics_dict["morans_i"] = None
        except Exception as e:
            print(f"⚠️ 计算 Moran's I 失败: {e}")
            import traceback
            traceback.print_exc()
            metrics_dict["morans_i"] = None
        
        print(f"✅ 聚类评估指标计算完成: CHAO={metrics_dict.get('chao')}, Silhouette={metrics_dict.get('silhouette')}, PAS={metrics_dict.get('pas')}, Moran's I={metrics_dict.get('morans_i')}")
        return metrics_dict
        
    except Exception as e:
        print(f"⚠️ 计算聚类评估指标时出错: {e}")
        import traceback
        traceback.print_exc()
        return {
            "chao": None,
            "silhouette": None,
            "pas": None,
            "morans_i": None
        }


def _compute_pas(labels, coords, k=6):
    """
    计算 PAS (Percentage of Abnormal Spots) - 异常点百分比
    
    异常点定义：某个spot的聚类标签与其大多数k-nearest neighbors的标签不同
    
    参数:
    --------
    labels : ndarray
        聚类标签（整数数组）
    coords : ndarray
        空间坐标 (N, 2)
    k : int
        k-nearest neighbors的数量
        
    返回:
    --------
    float
        PAS值 (0-1之间，值越小越好，表示异常点越少)
    """
    try:
        n = len(labels)
        if n < k + 1:
            return np.nan
        
        labels = np.asarray(labels).flatten()
        coords = np.asarray(coords)
        
        if coords.shape[1] != 2:
            raise ValueError("空间坐标必须是2维的")
        
        # 构建 k-nearest neighbors 图
        nn = NearestNeighbors(n_neighbors=k+1)  # +1 因为包括自己
        nn.fit(coords)
        distances, indices = nn.kneighbors(coords)
        
        # 统计异常点数量
        abnormal_count = 0
        
        for i in range(n):
            # 获取邻居索引（排除自己，即第一个）
            neighbor_indices = indices[i][1:]  # 跳过自己
            neighbor_labels = labels[neighbor_indices]
            
            # 当前spot的标签
            current_label = labels[i]
            
            # 计算邻居中最常见的标签
            unique_labels, counts = np.unique(neighbor_labels, return_counts=True)
            most_common_label = unique_labels[np.argmax(counts)]
            most_common_count = np.max(counts)
            
            # 如果当前标签与最常见的邻居标签不同，或者最常见标签的票数少于阈值
            # 则认为这是一个异常点
            # 阈值设为邻居数的一半（即大多数）
            threshold = len(neighbor_labels) / 2
            if current_label != most_common_label or most_common_count < threshold:
                abnormal_count += 1
        
        # PAS = 异常点数量 / 总spot数量
        pas = abnormal_count / n
        return pas
        
    except Exception as e:
        print(f"计算 PAS 时出错: {e}")
        import traceback
        traceback.print_exc()
        return np.nan


def _compute_morans_i(x, coords, k=6):
    """计算 Moran's I 空间自相关指数（优化版本）"""
    try:
        n = len(x)
        if n < 2:
            return np.nan
        
        x = np.asarray(x).flatten()
        x_mean = np.mean(x)
        x_dev = x - x_mean
        
        # 构建 k-nearest neighbors 权重矩阵（使用稀疏矩阵，更高效）
        W = kneighbors_graph(coords, k, mode='connectivity', include_self=False)
        W = W + W.T  # 对称化（稀疏矩阵）
        W.data = np.ones_like(W.data)  # 二值化权重
        
        w_sum = float(W.sum())
        if w_sum == 0:
            return np.nan
        
        # 使用矩阵乘法优化计算（避免双重循环）
        # num = sum(W[i,j] * x_dev[i] * x_dev[j]) for all i,j
        # = sum(W[i,j] * x_dev[i] * x_dev[j]) 
        # 可以写成矩阵形式: x_dev^T @ W @ x_dev
        # 但由于 W 是对称的，可以更高效地计算
        
        # 使用稀疏矩阵乘法优化
        num = float((x_dev @ W @ x_dev))
        
        denom = float(np.sum(x_dev ** 2))
        if denom == 0:
            return np.nan
        
        I = (n / w_sum) * (num / denom)
        return I
    except Exception as e:
        print(f"计算 Moran's I 时出错: {e}")
        import traceback
        traceback.print_exc()
        return np.nan


def _compute_gearys_c(x, coords, k=6):
    """计算 Geary's C 空间自相关指数（优化版本）
    
    Geary's C 公式:
    C = [(n-1) / (2*W)] * [sum(W[i,j] * (x[i] - x[j])^2)] / [sum((x[i] - x_mean)^2)]
    
    其中:
    - n 是样本数
    - W 是权重矩阵的总和
    - x[i] 是第 i 个样本的值
    - x_mean 是 x 的均值
    
    Geary's C 的范围通常是 0 到 2:
    - C < 1: 正空间自相关（相似值聚集）
    - C = 1: 无空间自相关（随机分布）
    - C > 1: 负空间自相关（相异值聚集）
    """
    try:
        n = len(x)
        if n < 2:
            return np.nan
        
        x = np.asarray(x).flatten()
        x_mean = np.mean(x)
        x_dev = x - x_mean
        
        # 构建 k-nearest neighbors 权重矩阵（使用稀疏矩阵，更高效）
        W = kneighbors_graph(coords, k, mode='connectivity', include_self=False)
        W = W + W.T  # 对称化（稀疏矩阵）
        W.data = np.ones_like(W.data)  # 二值化权重
        
        w_sum = float(W.sum())
        if w_sum == 0:
            return np.nan
        
        # 计算分子: sum(W[i,j] * (x[i] - x[j])^2)
        # 使用稀疏矩阵乘法优化
        # 对于对称矩阵 W，sum(W[i,j] * (x[i] - x[j])^2) = sum(W[i,j] * (x[i]^2 + x[j]^2 - 2*x[i]*x[j]))
        # = sum(W[i,j] * x[i]^2) + sum(W[i,j] * x[j]^2) - 2 * sum(W[i,j] * x[i] * x[j])
        # = 2 * sum(W[i,j] * x[i]^2) - 2 * sum(W[i,j] * x[i] * x[j])  (因为 W 对称)
        # = 2 * (sum((W @ np.ones(n)) * x^2) - sum((W @ x) * x))
        
        # 更直接的方法：使用稀疏矩阵的行求和
        W_rowsums = np.array(W.sum(axis=1)).flatten()
        num = float(2 * (np.sum(W_rowsums * (x ** 2)) - np.sum((W @ x) * x)))
        
        # 计算分母: sum((x[i] - x_mean)^2)
        denom = float(np.sum(x_dev ** 2))
        if denom == 0:
            return np.nan
        
        C = ((n - 1) / (2 * w_sum)) * (num / denom)
        return C
    except Exception as e:
        print(f"计算 Geary's C 时出错: {e}")
        import traceback
        traceback.print_exc()
        return np.nan


def compute_per_cluster_metrics(adata_obj, slice_id: str, cluster_result_id: str) -> List[dict]:
    """
    计算每个簇的指标：轮廓系数、Moran's I 和 Geary's C
    
    参数:
    --------
    adata_obj : AnnData
        包含聚类结果的 AnnData 对象
    slice_id : str
        切片ID
    cluster_result_id : str
        聚类结果ID
    
    返回:
    --------
    List[dict]
        每个簇的指标列表，格式: [
            {
                "cluster": "0",
                "silhouette": 0.75,
                "morans_i": 0.82,
                "gearys_c": 0.35,
                "size": 1234
            },
            ...
        ]
    """
    try:
        if adata_obj is None:
            raise ValueError("adata 对象为空，无法计算簇级指标。")
        
        adata_local = adata_obj.copy()
        
        # 获取聚类标签
        if "domain" not in adata_local.obs:
            print("⚠️ 缺少聚类标签信息（obs['domain']），无法计算指标。")
            raise ValueError("缺少聚类标签信息（obs['domain']），无法计算指标。")
        
        labels = adata_local.obs["domain"].astype(str)
        # 过滤掉 NaN 和空字符串
        valid_mask = ~labels.isna() & (labels != "") & (labels != "nan")
        valid_indices = np.where(valid_mask)[0]
        
        if len(valid_indices) == 0:
            print("⚠️ 没有有效的聚类标签，无法计算簇级指标")
            return []
        
        valid_labels = labels.iloc[valid_indices] if hasattr(labels, 'iloc') else labels[valid_mask]
        unique_labels = sorted(valid_labels.unique())
        print(f"   📊 发现 {len(unique_labels)} 个聚类: {unique_labels}")
        print(f"   📊 有效标签数: {len(valid_indices)}/{len(labels)}")
        label_to_int = {label: i for i, label in enumerate(unique_labels)}
        labels_int = np.array([label_to_int[label] for label in valid_labels])
        
        n_clusters = len(unique_labels)
        if n_clusters < 1:
            print("⚠️ 聚类数量为0，无法计算簇级指标")
            return []
        
        print(f"   📊 有效样本数: {len(labels_int)}, 聚类数: {n_clusters}")
        
        # 获取特征矩阵（使用 embedding 或 PCA）
        if "emb" in adata_local.obsm:
            X = adata_local.obsm["emb"]
        elif "X_pca" in adata_local.obsm:
            X = adata_local.obsm["X_pca"]
        else:
            # 如果没有 embedding，使用原始表达数据的前50个PC
            adata_temp = adata_local.copy()
            if not (adata_temp.X.min() >= 0 and adata_temp.X.max() <= 20):
                sc.pp.normalize_total(adata_temp)
                sc.pp.log1p(adata_temp)
            sc.pp.pca(adata_temp, n_comps=min(50, adata_temp.n_obs - 1, adata_temp.n_vars))
            X = adata_temp.obsm["X_pca"]
        
        # 转换为 numpy array
        if hasattr(X, "toarray"):
            X = X.toarray()
        X = np.asarray(X)
        
        # 确保 X 和 labels_int 的长度匹配（只使用有效标签）
        if len(X) != len(valid_labels):
            print(f"⚠️ 特征矩阵长度 ({len(X)}) 与有效标签长度 ({len(valid_labels)}) 不匹配，尝试对齐...")
            # 只使用有效标签对应的特征
            X = X[valid_indices]
            if len(X) != len(labels_int):
                print(f"⚠️ 对齐后长度仍不匹配: X={len(X)}, labels={len(labels_int)}")
                return []
        
        print(f"   📊 特征矩阵形状: {X.shape}, 标签数: {len(labels_int)}")
        
        # 获取空间坐标
        has_spatial = "spatial" in adata_local.obsm
        if has_spatial:
            spatial_coords = adata_local.obsm["spatial"]
            # 也需要对齐空间坐标
            if len(spatial_coords) != len(labels_int):
                spatial_coords = np.asarray(spatial_coords)[valid_indices]
            else:
                spatial_coords = np.asarray(spatial_coords)
            print(f"   📊 空间坐标形状: {spatial_coords.shape}")
        else:
            print("⚠️ 缺少空间坐标信息，无法计算 Moran's I 和 Geary's C")
        
        # 计算每个样本的轮廓系数（需要至少2个簇）
        silhouette_samples_scores = None
        if n_clusters >= 2:
            try:
                print(f"   🔢 计算每个样本的轮廓系数...")
                silhouette_samples_scores = silhouette_samples(X, labels_int)
                print(f"   ✅ 样本轮廓系数计算完成")
            except Exception as e:
                print(f"⚠️ 计算样本轮廓系数失败: {e}")
                silhouette_samples_scores = None
        
        cluster_metrics = []
        
        # 对每个簇计算指标
        for label_idx, label in enumerate(unique_labels):
            cluster_mask = labels_int == label_idx
            cluster_size = int(np.sum(cluster_mask))
            
            if cluster_size == 0:
                continue
            
            metrics_dict = {
                "cluster": str(label),
                "size": cluster_size,
                "silhouette": None,
                "morans_i": None,
                "gearys_c": None
            }
            
            # 1. 计算簇级轮廓系数（该簇内所有样本的平均轮廓系数）
            if silhouette_samples_scores is not None:
                try:
                    cluster_silhouette_scores = silhouette_samples_scores[cluster_mask]
                    metrics_dict["silhouette"] = float(np.mean(cluster_silhouette_scores))
                except Exception as e:
                    print(f"⚠️ 计算簇 {label} 的轮廓系数失败: {e}")
            
            # 2. 计算 Moran's I（需要至少2个样本且必须有空间坐标）
            if has_spatial and cluster_size >= 2:
                try:
                    # one-hot 编码：属于该簇的为1，否则为0
                    onehot = (labels_int == label_idx).astype(float)
                    print(f"   📊 计算簇 {label} 的 Moran's I...")
                    moran_i = _compute_morans_i(onehot, spatial_coords, k=6)
                    if not np.isnan(moran_i):
                        metrics_dict["morans_i"] = float(moran_i)
                        print(f"   ✅ 簇 {label} 的 Moran's I = {moran_i:.4f}")
                except Exception as e:
                    print(f"⚠️ 计算簇 {label} 的 Moran's I 失败: {e}")
            
            # 3. 计算 Geary's C（需要至少2个样本且必须有空间坐标）
            if has_spatial and cluster_size >= 2:
                try:
                    # one-hot 编码：属于该簇的为1，否则为0
                    onehot = (labels_int == label_idx).astype(float)
                    print(f"   📊 计算簇 {label} 的 Geary's C...")
                    gearys_c = _compute_gearys_c(onehot, spatial_coords, k=6)
                    if not np.isnan(gearys_c):
                        metrics_dict["gearys_c"] = float(gearys_c)
                        print(f"   ✅ 簇 {label} 的 Geary's C = {gearys_c:.4f}")
                except Exception as e:
                    print(f"⚠️ 计算簇 {label} 的 Geary's C 失败: {e}")
            
            # Sanitize all float values before appending
            metrics_dict["silhouette"] = sanitize_float_for_json(metrics_dict["silhouette"])
            metrics_dict["morans_i"] = sanitize_float_for_json(metrics_dict["morans_i"])
            metrics_dict["gearys_c"] = sanitize_float_for_json(metrics_dict["gearys_c"])
            
            cluster_metrics.append(metrics_dict)
        
        print(f"✅ 簇级指标计算完成，共 {len(cluster_metrics)} 个簇")
        if len(cluster_metrics) == 0:
            print("⚠️ 警告：没有计算出任何簇级指标，可能的原因：")
            print(f"   - 聚类数量: {n_clusters}")
            print(f"   - 有效标签数: {len(labels_int)}")
            print(f"   - 是否有空间坐标: {has_spatial}")
        return cluster_metrics
        
    except Exception as e:
        print(f"⚠️ 计算簇级指标时出错: {e}")
        import traceback
        traceback.print_exc()
        return []


def store_per_cluster_metrics(cluster_metrics: List[dict], slice_id: str, cluster_result_id: str) -> None:
    """
    将簇级指标存储到数据库
    
    参数:
    --------
    cluster_metrics : List[dict]
        每个簇的指标列表
    slice_id : str
        切片ID
    cluster_result_id : str
        聚类结果ID
    """
    try:
        if not cluster_metrics:
            print("⚠️ 没有簇级指标需要存储")
            return
        
        metadata = MetaData()
        metadata.reflect(bind=engine, only=["cluster_metrics"])
        cluster_metrics_table = metadata.tables["cluster_metrics"]
        
        with engine.begin() as conn:
            # 先删除该聚类结果的旧指标
            conn.execute(
                cluster_metrics_table.delete().where(
                    cluster_metrics_table.c.slice_id == slice_id,
                    cluster_metrics_table.c.cluster_result_id == cluster_result_id
                )
            )
            
                # 插入新指标
            inserted_count = 0
            for metrics in cluster_metrics:
                try:
                    data = {
                        "slice_id": slice_id,
                        "cluster_result_id": cluster_result_id,
                        "cluster": str(metrics["cluster"]),  # 确保是字符串
                        "size": metrics.get("size"),
                        "silhouette": metrics.get("silhouette"),
                        "morans_i": metrics.get("morans_i"),
                        "gearys_c": metrics.get("gearys_c"),
                        "updated_at": func.current_timestamp()
                    }
                    
                    # 使用 INSERT OR REPLACE 确保幂等性
                    conn.execute(
                        cluster_metrics_table.insert().values(**data)
                    )
                    inserted_count += 1
                except Exception as e:
                    print(f"⚠️ 插入簇级指标失败 (cluster={metrics.get('cluster')}): {e}")
                    import traceback
                    traceback.print_exc()
        
        print(f"✅ 簇级指标已存储到数据库，共 {inserted_count}/{len(cluster_metrics)} 个簇成功插入")
    except Exception as e:
        print(f"⚠️ 存储簇级指标时出错: {e}")
        import traceback
        traceback.print_exc()


def _compute_umap_in_subprocess(emb: np.ndarray, barcodes, timeout: int = 600) -> np.ndarray:
    """
    在子进程中计算 UMAP（NUMBA_THREADING_LAYER=workqueue），避免 TBB 在非主线程 fork 卡死。
    返回 X_umap shape (n_obs, 2)。数据量大时约 30 秒–2 分钟属正常。
    """
    n_obs = emb.shape[0]
    print(f"🔄 UMAP 计算中（spot 数={n_obs}，约 30 秒–2 分钟，请勿关闭）...", flush=True)
    worker_dir = os.path.dirname(os.path.abspath(__file__))
    worker_script = os.path.join(worker_dir, "umap_worker.py")
    env = os.environ.copy()
    env["NUMBA_THREADING_LAYER"] = "workqueue"
    with tempfile.TemporaryDirectory(prefix="umap_") as tmpdir:
        in_npz = os.path.join(tmpdir, "in.npz")
        out_npz = os.path.join(tmpdir, "out.npz")
        np.savez(in_npz, emb=emb.astype(np.float64), barcodes=np.array(barcodes))
        proc = subprocess.run(
            [sys.executable, worker_script, in_npz, out_npz],
            env=env,
            cwd=worker_dir,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"UMAP 子进程失败 (exit {proc.returncode})，请查看上方日志")
        out_data = np.load(out_npz, allow_pickle=True)
        X_umap = out_data["X_umap"]
        out_data.close()
    print("✅ UMAP 计算完成", flush=True)
    return X_umap


def compute_and_store_umap(adata_obj, slice_id: str, cluster_result_id: str) -> None:
    """
    根据当前聚类结果计算 UMAP，并将坐标写入数据库，供前端后续直接使用。
    """
    global adata

    if adata_obj is None:
        raise ValueError("adata 对象为空，无法计算 UMAP。")

    adata_local = adata_obj.copy()

    if "emb" not in adata_local.obsm:
        raise ValueError("缺少 embedding 信息（obsm['emb']），无法计算 UMAP。")

    if "X_umap" not in adata_local.obsm:
        print("🔄 正在为新聚类结果计算 UMAP ...")
        emb = adata_local.obsm["emb"]
        barcodes = adata_local.obs_names.tolist()
        adata_local.obsm["X_umap"] = _compute_umap_in_subprocess(emb, barcodes)

    adata.obsm["X_umap"] = adata_local.obsm["X_umap"]
    adata.uns["neighbors"] = adata_local.uns.get("neighbors", {})

    table_name = f"spot_cluster_{slice_id}"
    metadata = MetaData()
    metadata.reflect(bind=engine)
    if table_name not in metadata.tables:
        raise ValueError(f"数据表 {table_name} 不存在，无法保存 UMAP。")

    spot_cluster = metadata.tables[table_name]

    umap_x_col = f"umap_1_{cluster_result_id}"
    umap_y_col = f"umap_2_{cluster_result_id}"

    with engine.connect() as conn:
        cols = conn.execute(text(f"PRAGMA table_info(`{table_name}`)")).fetchall()
        existing_cols = [col[1] for col in cols]
        if umap_x_col not in existing_cols:
            conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `{umap_x_col}` FLOAT"))
        if umap_y_col not in existing_cols:
            conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `{umap_y_col}` FLOAT"))
        conn.commit()

    umap_coords = adata_local.obsm["X_umap"]
    update_rows = []
    for i, barcode in enumerate(adata_local.obs_names):
        if i < len(umap_coords):
            update_rows.append(
                {
                    "barcode": barcode,
                    "cluster_result_id": cluster_result_id,
                    "umap_1": float(umap_coords[i, 0]),
                    "umap_2": float(umap_coords[i, 1]),
                }
            )

    with engine.begin() as conn:
        batch_size = 100
        for i in range(0, len(update_rows), batch_size):
            batch = update_rows[i : i + batch_size]
            for row in batch:
                conn.execute(
                    update(spot_cluster)
                    .where(
                        spot_cluster.c.barcode == row["barcode"],
                        spot_cluster.c.cluster_result_id == row["cluster_result_id"],
                    )
                    .values(**{umap_x_col: row["umap_1"], umap_y_col: row["umap_2"]})
                )
    print(f"✅ UMAP 坐标已写入数据库（cluster_result_id={cluster_result_id}）。")
    
@app.post("/run-clustering")
def run_clustering(request: ClusteringRequest):
    '''
    根据前端设置的聚类方法和参数进行聚类
    '''
    global adata, slice_id, path

    # 自动生成 cluster_result_id 和 result_name
    if not request.cluster_result_id or request.cluster_result_id == "default":
        auto_id = f"{request.slice_id}_{request.method}_{request.n_clusters}_{request.epoch}"
        request.cluster_result_id = auto_id

    if not request.result_name:
        request.result_name = f"{request.method} (k={request.n_clusters}, epoch={request.epoch})"

    # ⚠️ 无论是否相同，都重新加载一次（强制刷新特征和数据）
    print(f"🔁 加载切片: {request.slice_id}")
    slice_id = request.slice_id
    path = f"./data/{slice_id}"
    prepare_data(force_reload=True)  # ✅ 强制重新加载，避免使用旧特征
    print("✅ 切片数据已加载完毕")

    adata_local = adata.copy()
    if adata_local is None:
        raise HTTPException(status_code=500, detail="❌ adata 加载失败")

    print(f"⚙️ 聚类参数: method={request.method}, n_clusters={request.n_clusters}, epoch={request.epoch}")

    # ✅ 执行指定聚类方法
    if request.method == "SEDR":
        adata_local = run_SEDR_and_clustering(adata_local, n_clusters=request.n_clusters, method=request.method, epoch=request.epoch)
    elif request.method == "GraphST":
        adata_local = run_graphst_and_clustering(adata_local, n_clusters=request.n_clusters, method=request.method, epoch=request.epoch)
    elif request.method == "SpaGCN":
        adata_local = run_SpaGCN_and_clustering(adata_local, n_clusters=request.n_clusters, method=request.method, epoch=request.epoch)
    else:
        raise HTTPException(status_code=400, detail=f"❌ 不支持的聚类方法: {request.method}")

    if "emb" not in adata_local.obsm:
        print("⚠️ 未找到通用 embedding，使用 PCA 结果作为替代")
        adata_temp = adata_local.copy()
        sc.pp.normalize_total(adata_temp)
        sc.pp.log1p(adata_temp)
        sc.pp.pca(adata_temp, n_comps=min(50, adata_temp.n_obs - 1, adata_temp.n_vars))
        adata_local.obsm["emb"] = adata_temp.obsm["X_pca"].copy()

    # 更新全局 adata
    adata = adata_local
    print("📊 聚类完成，分布如下：")
    print(adata_local.obs["domain"].value_counts())

    # ✅ 批量写入数据库（cluster + embedding）
    metadata = MetaData()
    metadata.reflect(bind=engine)
    table_name = f"spot_cluster_{request.slice_id}"
    spot_cluster = metadata.tables[table_name]

    update_data = []
    for i, (barcode, row) in enumerate(adata_local.obs.iterrows()):
        domain_val = row.get('domain')
        cluster = str(domain_val)
        try:
            domain_float = float(domain_val)
            if math.isfinite(domain_float):
                if domain_float.is_integer():
                    cluster = str(int(domain_float))
                else:
                    cluster = ("%g" % domain_float)
        except Exception:
            cluster = str(domain_val)
        emb_vec = adata_local.obsm["emb"][i]
        emb_str = ",".join(map(str, emb_vec))
        x_val = float(row.get("x", 0.0))
        y_val = float(row.get("y", 0.0))
        update_data.append({
            "barcode": barcode,
            "cluster_result_id": request.cluster_result_id,
            "cluster": cluster,
            "x": x_val,
            "y": y_val,
            "emb": emb_str
        })

    with engine.begin() as conn:
        for data in update_data:
            exists = conn.execute(
                select(spot_cluster).where(
                    spot_cluster.c.barcode == data["barcode"],
                    spot_cluster.c.cluster_result_id == data["cluster_result_id"]
                )
            ).fetchone()

            if exists:
                conn.execute(
                    update(spot_cluster)
                    .where(
                        spot_cluster.c.barcode == data["barcode"],
                        spot_cluster.c.cluster_result_id == data["cluster_result_id"]
                    )
                    .values(
                        cluster=data["cluster"],
                        emb=data["emb"],
                        x=data["x"],
                        y=data["y"]
                    )
                )
            else:
                conn.execute(insert(spot_cluster).values(data))

    print(f"✅ 写入数据库完毕：{len(update_data)} 条记录")

    # ✅ 更新 cluster_method 与清空 cluster_log
    with engine.begin() as conn:
        cluster_log = Table("cluster_log", metadata, autoload_with=engine)
        conn.execute(cluster_log.delete().where(
            cluster_log.c.slice_id == request.slice_id,
            cluster_log.c.cluster_result_id == request.cluster_result_id
        ))

        cluster_method = Table("cluster_method", metadata, autoload_with=engine)
        exists = conn.execute(
            select(cluster_method).where(
                cluster_method.c.slice_id == request.slice_id,
                cluster_method.c.cluster_result_id == request.cluster_result_id
            )
        ).fetchone()

        # 保留已有的 metrics 字段（如果存在）
        existing_metrics = None
        if exists:
            # 从已有记录中提取 metrics 字段
            if hasattr(exists, 'chao') or (isinstance(exists, tuple) and len(exists) > 6):
                try:
                    chao = exists.chao if hasattr(exists, 'chao') else (exists[6] if len(exists) > 6 else None)
                    silhouette = exists.silhouette if hasattr(exists, 'silhouette') else (exists[7] if len(exists) > 7 else None)
                    pas = exists.pas if hasattr(exists, 'pas') else (exists[8] if len(exists) > 8 else None)
                    morans_i = exists.morans_i if hasattr(exists, 'morans_i') else (exists[9] if len(exists) > 9 else None)
                    # 如果至少有一个指标不是 None，保留它们
                    if any(v is not None for v in [chao, silhouette, pas, morans_i]):
                        existing_metrics = {
                            'chao': chao,
                            'silhouette': silhouette,
                            'pas': pas,
                            'morans_i': morans_i
                        }
                except Exception as e:
                    print(f"⚠️ 读取已有 metrics 时出错: {e}")

        data = {
            "slice_id": request.slice_id,
            "cluster_result_id": request.cluster_result_id,
            "result_name": request.result_name or f"{request.method}_{request.n_clusters}clusters",
            "method": request.method,
            "n_clusters": request.n_clusters,
            "epoch": request.epoch,
        }

        if exists:
            # 更新时保留已有的 metrics
            update_values = {
                "result_name": data["result_name"],
                "method": data["method"],
                "n_clusters": data["n_clusters"],
                "epoch": data["epoch"],
                "updated_at": func.current_timestamp()
            }
            # 如果有已有的 metrics，保留它们（但不在这里更新，等计算完再更新）
            conn.execute(
                update(cluster_method)
                .where(
                    cluster_method.c.slice_id == request.slice_id,
                    cluster_method.c.cluster_result_id == request.cluster_result_id
                )
                .values(**update_values)
            )
        else:
            conn.execute(insert(cluster_method).values(data))

    print("✅ 聚类方法信息已记录")

    # ✅ 生成并保存聚类结果的 plot
    plot_path = save_cluster_plot(request.slice_id, request.cluster_result_id)
    if plot_path:
        # 更新 cluster_method 表中的 plot_path
        with engine.begin() as conn:
            conn.execute(
                update(cluster_method)
                .where(
                    cluster_method.c.slice_id == request.slice_id,
                    cluster_method.c.cluster_result_id == request.cluster_result_id
                )
                .values(plot_path=plot_path)
            )
        print(f"✅ Plot 已保存: {plot_path}")

    # ✅ 预先计算并保存 UMAP 坐标，供后续直接使用
    try:
        compute_and_store_umap(
            adata_local,
            request.slice_id,
            request.cluster_result_id,
        )
    except Exception as e:
        print(f"⚠️ 预计算 UMAP 失败: {e}")
    
    # ✅ 计算并保存聚类评估指标
    try:
        metrics = compute_clustering_metrics(
            adata_local,
            request.slice_id,
            request.cluster_result_id,
        )
        
        # ✅ 计算并存储簇级指标
        try:
            print(f"   🔢 开始计算簇级指标...")
            cluster_metrics = compute_per_cluster_metrics(
                adata_local,
                request.slice_id,
                request.cluster_result_id,
            )
            if cluster_metrics:
                store_per_cluster_metrics(
                    cluster_metrics,
                    request.slice_id,
                    request.cluster_result_id,
                )
                print(f"   ✅ 簇级指标计算完成，共 {len(cluster_metrics)} 个簇")
            else:
                print(f"   ⚠️ 没有簇级指标需要存储")
        except Exception as e:
            print(f"⚠️ 计算簇级指标时出错: {e}")
            import traceback
            traceback.print_exc()
        
        # 更新 cluster_method 表中的指标（需要先 reflect 表结构）
        metadata.reflect(bind=engine, only=["cluster_method"])
        cluster_method = metadata.tables["cluster_method"]
        
        with engine.begin() as conn:
            # 使用 SQL 更新，只更新非 None 的指标字段，保留已有的其他指标
            update_fields = {}
            if metrics.get("chao") is not None:
                update_fields["chao"] = metrics.get("chao")
            if metrics.get("silhouette") is not None:
                update_fields["silhouette"] = metrics.get("silhouette")
            if metrics.get("pas") is not None:
                update_fields["pas"] = metrics.get("pas")
            if metrics.get("morans_i") is not None:
                update_fields["morans_i"] = metrics.get("morans_i")
            
            # 只更新有值的字段
            if update_fields:
                conn.execute(
                    update(cluster_method)
                    .where(
                        cluster_method.c.slice_id == request.slice_id,
                        cluster_method.c.cluster_result_id == request.cluster_result_id
                    )
                    .values(**update_fields)
                )
        print(f"✅ 聚类评估指标已保存到数据库")
    except Exception as e:
        print(f"⚠️ 计算或保存聚类评估指标失败: {e}")
        import traceback
        traceback.print_exc()

    # ✅ 自动对齐标签：将新聚类结果对齐到最早的聚类结果
    try:
        metadata.reflect(bind=engine, only=["cluster_method"])
        cluster_method = metadata.tables["cluster_method"]
        
        with engine.connect() as conn:
            # 查询该切片的所有聚类结果，按创建时间排序（最早的在前）
            stmt = select(
                cluster_method.c.cluster_result_id,
                cluster_method.c.updated_at
            ).where(
                cluster_method.c.slice_id == request.slice_id
            ).order_by(cluster_method.c.updated_at.asc())
            
            all_results = conn.execute(stmt).fetchall()
            
            if len(all_results) > 1:
                # 找到最早的聚类结果（第一个）
                earliest_result_id = all_results[0].cluster_result_id
                
                # 如果当前结果不是最早的，则对齐到最早的
                if earliest_result_id != request.cluster_result_id:
                    print(f"🔄 自动对齐标签：将新结果 '{request.cluster_result_id}' 对齐到最早的聚类结果 '{earliest_result_id}'")
                    try:
                        alignment_result = apply_label_alignment(
                            slice_id=request.slice_id,
                            source_cluster_result_id=request.cluster_result_id,
                            target_cluster_result_id=earliest_result_id,
                            new_cluster_result_id=None  # 更新当前结果
                        )
                        print(f"✅ 自动对齐完成，准确率: {alignment_result['alignment_accuracy']:.2%}")
                    except Exception as e:
                        print(f"⚠️ 自动对齐失败: {e}")
                        # 不抛出异常，避免影响聚类结果返回
                else:
                    print(f"ℹ️ 当前结果是最早的聚类结果，无需对齐")
            else:
                print(f"ℹ️ 这是第一个聚类结果，无需对齐")
    except Exception as e:
        print(f"⚠️ 自动对齐过程出错: {e}")
        # 不抛出异常，避免影响聚类结果返回

    # ✅ 返回最新的可视化数据
    return get_plot_data(request.slice_id, request.cluster_result_id)

def run_graphst_and_clustering(adata_local, n_clusters=7, radius=50, method="mclust", refinement=True, epoch=500):
    print("⚙️ 执行 GraphST 模型训练与聚类...")
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    # 原始数据备份（完整的）
    adata_full = adata_local.copy()

    # GraphST 模型训练（这一步可能过滤掉部分点）
    model = GraphST.GraphST(adata_local, device=device, epochs=epoch)
    adata_embedded = model.train()

    # 检查 embedding 是否存在
    if "emb" in adata_embedded.obsm:
        print("✅ GraphST 输出维度:", adata_embedded.obsm["emb"].shape)
    else:
        print("❌ 没有发现 obsm['emb']，聚类可能失败")

    # 聚类（在 adata_embedded 上）
    # ⚠️ 注意：这里的 method 应该是聚类方法（mclust/leiden/louvain），而不是深度学习方法名
    clustering_method = "mclust"  # GraphST 默认使用 mclust 聚类
    print(f"聚类参数：n_clusters={n_clusters}, clustering_method={clustering_method}")
    clustering(adata_embedded, n_clusters=n_clusters, radius=radius, method=clustering_method, refinement=refinement)

    # 将 obsm["emb"] 回填到完整数据
    embedding = pd.DataFrame(adata_embedded.obsm["emb"], index=adata_embedded.obs_names)
    emb_dim = embedding.shape[1]
    emb_matrix = np.zeros((len(adata_full.obs_names), emb_dim))

    for i, barcode in enumerate(adata_full.obs_names):
        if barcode in embedding.index:
            emb_matrix[i] = embedding.loc[barcode]
    adata_full.obsm["emb"] = emb_matrix

    # domain 聚类结果也回填
    domain_series = adata_embedded.obs["domain"]
    adata_full.obs["domain"] = domain_series.reindex(adata_full.obs_names)
    adata_full.obs["domain"] = pd.to_numeric(adata_full.obs["domain"], errors="coerce").apply(
        lambda x: f"{x:.1f}" if pd.notnull(x) else "nan"
    )
    adata_full.obs["domain"] = adata_full.obs["domain"].astype("category")
    adata_full.obs["leiden_original"] = adata_full.obs["domain"].copy()

    # ✅ 聚类分布检查
    print("📊 聚类分布情况：\n", adata_full.obs["domain"].value_counts())

    return adata_full

def run_SEDR_and_clustering(adata, n_clusters=7, radius=50, method="mclust", refinement=False,epoch = 500):
    print("⚙️ 执行 SEDR 模型训练与聚类...")
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    # model = SEDR.SEDR(adata_local, device=device, epochs=epoch)
    # adata_local = model.train()
    adata.layers['count'] = adata.X.toarray()
    sc.pp.filter_genes(adata, min_cells=50)
    sc.pp.filter_genes(adata, min_counts=10)
    sc.pp.normalize_total(adata, target_sum=1e6)
    sc.pp.highly_variable_genes(adata, flavor="seurat_v3", layer='count', n_top_genes=2000)
    adata = adata[:, adata.var['highly_variable'] == True]
    sc.pp.scale(adata)

    adata_X = PCA(n_components=200, random_state=42).fit_transform(adata.X)
    adata.obsm['X_pca'] = adata_X
    graph_dict = SEDR.graph_construction(adata, 12)
    print(graph_dict)
    sedr_net = SEDR.Sedr(adata.obsm['X_pca'], graph_dict, mode='clustering', device=device)
    using_dec = True
    if using_dec:
        sedr_net.train_with_dec(N=1)
    else:
        sedr_net.train_without_dec(N=1)
    sedr_feat, _, _, _ = sedr_net.process()
    adata.obsm['SEDR'] = sedr_feat
    SEDR.mclust_R(adata, n_clusters, use_rep='SEDR', key_added='SEDR')
    adata.obs["domain"] = adata.obs["SEDR"].astype(int)
    adata.obsm['emb'] = sedr_feat

    return adata


def run_SpaGCN_and_clustering(adata2, n_clusters=7):
    print("⚙️ 执行 SpaGCN 模型训练与聚类...")

    spatial=pd.read_csv("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/151673/spatial/tissue_positions_list.csv", header=None,index_col=0) 
    adata2.obs["x1"]=spatial[1]
    adata2.obs["x2"]=spatial[2]
    adata2.obs["x3"]=spatial[3]
    adata2.obs["x4"]=spatial[4]
    adata2.obs["x5"]=spatial[5]
    adata2.obs["x_array"]=adata2.obs["x2"]
    adata2.obs["y_array"]=adata2.obs["x3"]
    adata2.obs["x_pixel"]=adata2.obs["x4"]
    adata2.obs["y_pixel"]=adata2.obs["x5"]
    #Select captured samples
    adata2=adata2[adata2.obs["x1"]==1]
    adata2.var_names=[i.upper() for i in list(adata2.var_names)]
    adata2.var["genename"]=adata2.var.index.astype("str")

    img=cv2.imread("/home/junning/projectnvme/spatial-omics-vis/version1/spatial-omics-vis/omics-backend/data/151673/spatial/full_image.tif")
    x_array=adata2.obs["x_array"].tolist()
    y_array=adata2.obs["y_array"].tolist()
    x_pixel=adata2.obs["x_pixel"].tolist()
    y_pixel=adata2.obs["y_pixel"].tolist()

    img_new=img.copy()
    s=1
    b=49
    adj=spg.calculate_adj_matrix(x=x_pixel,y=y_pixel, x_pixel=x_pixel, y_pixel=y_pixel, image=img, beta=b, alpha=s, histology=True)
    spg.prefilter_genes(adata2,min_cells=3) # avoiding all genes are zeros
    spg.prefilter_specialgenes(adata2)
    #Normalize and take log for UMI
    sc.pp.normalize_per_cell(adata2)
    sc.pp.log1p(adata2)
    p=0.5 
    l=spg.search_l(p, adj, start=0.01, end=1000, tol=0.001, max_run=100)
    n_clusters=7
    r_seed=t_seed=n_seed=100
    res=spg.search_res(adata2, adj, l, n_clusters, start=0.7, step=0.1, tol=5e-3, lr=0.05, max_epochs=20, r_seed=r_seed, t_seed=t_seed, n_seed=n_seed)

    clf=spg.SpaGCN()
    clf.set_l(l)
    random.seed(r_seed)
    torch.manual_seed(t_seed)
    np.random.seed(n_seed)
    result = clf.train(adata2,adj,init_spa=True,init="louvain",res=res, tol=5e-3, lr=0.05, max_epochs=900)
    adata2.obsm["spagcn_embed"] = clf.embed
    adata2.obsm["emb"] = clf.embed

    y_pred, prob=clf.predict()
    adata2.obs["pred"]= y_pred
    adata2.obs["pred"]=adata2.obs["pred"].astype('category')
    #Do cluster refinement(optional)
    #shape="hexagon" for Visium data, "square" for ST data.
    adj_2d=spg.calculate_adj_matrix(x=x_array,y=y_array, histology=False)
    refined_pred=spg.refine(sample_id=adata2.obs.index.tolist(), pred=adata2.obs["pred"].tolist(), dis=adj_2d, shape="hexagon")
    adata2.obs["refined_pred"]=refined_pred
    adata2.obs["domain"]=adata2.obs["refined_pred"].astype('category')
    return adata2


@app.get("/allslices")
def get_all_slice_ids(data_root="./data"):
    folders = [
        name for name in os.listdir(data_root)
        if os.path.isdir(os.path.join(data_root, name))
    ]
    # 自定义排序规则：数字开头的优先，按数字排序；否则按字母顺序
    folders.sort(key=lambda name: (not name[0].isdigit(), name))
    return folders


class ClearSliceDataRequest(BaseModel):
    slice_ids: List[str]


def _safe_slice_id(sid: str) -> bool:
    """只允许字母数字、点、下划线、横线，防止 SQL 注入。"""
    return bool(sid) and all(c.isalnum() or c in "._-" for c in sid)


@app.post("/admin/clear-slice-data")
def clear_slice_data(request: ClearSliceDataRequest):
    """
    清空指定切片的数据库数据，便于重新加载（如修正坐标后重来）。
    会删除：spot_cluster_{slice_id} 全表数据、cluster_method / cluster_log 中该 slice 的记录。
    清空后切换到该切片会触发 prepare_data 重新从 CSV 插入 default 数据。
    """
    slice_ids = [s.strip() for s in request.slice_ids if s and _safe_slice_id(s.strip())]
    if not slice_ids:
        raise HTTPException(status_code=400, detail="请提供有效的 slice_ids（如 ['slice1', 'slice1.4']）")
    with engine.begin() as conn:
        for sid in slice_ids:
            table_name = f"spot_cluster_{sid}"
            quoted = f'"{table_name}"'
            try:
                conn.execute(text(f"DELETE FROM {quoted}"))
            except Exception as e:
                if "no such table" in str(e).lower():
                    pass
                else:
                    raise
        for sid in slice_ids:
            conn.execute(text("DELETE FROM cluster_method WHERE slice_id = :sid"), {"sid": sid})
        for sid in slice_ids:
            conn.execute(text("DELETE FROM cluster_log WHERE slice_id = :sid"), {"sid": sid})
    return {"ok": True, "slice_ids": slice_ids, "message": "已清空，请切换到对应切片以重新加载数据"}


@app.on_event("startup")
def load_once():
    global slice_id,spatial_dir,sf,scale_key,factor,path
    slice_id = get_all_slice_ids()[0]
    spatial_dir = os.path.join(f"./data/{slice_id}", "spatial")
    with open(os.path.join(spatial_dir, "scalefactors_json.json"), "r") as f:
        sf = json.load(f)
    scale_key = "tissue_hires_scalef" if scale == "hires" else "tissue_lowres_scalef"
    path = f"./data/{slice_id}"
    factor = sf[scale_key]
    print("目前加载的是切片:",slice_id)
    prepare_data()

def _which_tissue_image(slice_id: str) -> str | None:
    """与 get_image 一致：返回实际使用的图片名（hires 优先，否则 lowres）。"""
    spatial_dir = Path(f"data/{slice_id}/spatial")
    if (spatial_dir / "tissue_hires_image.png").exists():
        return "tissue_hires_image.png"
    if (spatial_dir / "tissue_lowres_image.png").exists():
        return "tissue_lowres_image.png"
    return None


def get_scalefactor(slice_id: str, use_hires: bool | None = None) -> float:
    """缩放因子需与前端显示的图一致：有 hires 用 hires 因子，只有 lowres 用 lowres 因子。
    支持 10x 标准 key（*scalefactor）与简写（*scalef）。"""
    path = Path(f"data/{slice_id}/spatial/scalefactors_json.json")
    if not path.exists():
        return 1.0
    with open(path) as f:
        sf = json.load(f)
    if use_hires is None:
        use_hires = _which_tissue_image(slice_id) == "tissue_hires_image.png"
    if use_hires:
        return sf.get("tissue_hires_scalefactor") or sf.get("tissue_hires_scalef", 1.0)
    return sf.get("tissue_lowres_scalefactor") or sf.get("tissue_lowres_scalef", 1.0)


def _get_tissue_image_size(slice_id: str) -> tuple[float, float] | None:
    """返回 (width, height)，与前端显示的 tissue 图一致（hires 优先，否则 lowres）。"""
    spatial_dir = Path(f"data/{slice_id}/spatial")
    for name in ("tissue_hires_image.png", "tissue_lowres_image.png"):
        path = spatial_dir / name
        if path.exists():
            try:
                img = plt.imread(str(path))
                if hasattr(img, "shape") and len(img.shape) >= 2:
                    h, w = img.shape[:2]
                    return float(w), float(h)
            except Exception as e:
                print(f"⚠️ 读取图片尺寸失败 {path}: {e}")
    return None

def save_cluster_plot(slice_id: str, cluster_result_id: str) -> str:
    """
    生成并保存聚类结果的空间分布图
    返回保存的图片路径（相对路径）
    """
    try:
        # 确保 plots 目录存在
        plots_dir = Path(f"data/{slice_id}/plots")
        plots_dir.mkdir(parents=True, exist_ok=True)
        
        # 从数据库读取数据
        table_name = f"spot_cluster_{slice_id}"
        factor = get_scalefactor(slice_id)
        
        query = text(f"""
            SELECT barcode, cluster, x, y 
            FROM `{table_name}` 
            WHERE cluster_result_id = :cluster_result_id
        """)
        
        with engine.connect() as conn:
            rows = conn.execute(query, {"cluster_result_id": cluster_result_id}).fetchall()
        
        if not rows:
            print("⚠️ 没有找到聚类数据，跳过 plot 生成")
            return None
        
        # 构造 DataFrame
        df = pd.DataFrame(rows, columns=["barcode", "cluster", "x", "y"])
        df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["x", "y"])
        
        if df.empty:
            print("⚠️ 数据为空，跳过 plot 生成")
            return None
        
        # 应用缩放因子（与 get_plot_data 一致）
        df["x"] = df["x"] * factor
        df["y"] = df["y"] * factor
        img_size = _get_tissue_image_size(slice_id)
        if img_size is not None:
            img_w, img_h = img_size
            x_range = df["x"].max() - df["x"].min()
            y_range = df["y"].max() - df["y"].min()
            fit_xy = abs(x_range - img_w) + abs(y_range - img_h)
            fit_yx = abs(x_range - img_h) + abs(y_range - img_w)
            if fit_yx < fit_xy:
                df["x"], df["y"] = df["y"].copy(), df["x"].copy()
            df["y"] = img_h - df["y"]
        
        # 生成 plot，保持与前端一致的比例与样式
        x_min, x_max = df["x"].min(), df["x"].max()
        y_min, y_max = df["y"].min(), df["y"].max()
        width = max(x_max - x_min, 1e-6)
        height = max(y_max - y_min, 1e-6)
        aspect = height / width

        base_size = 6
        fig_width = base_size
        fig_height = base_size * aspect
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))

        # 为每个 cluster 绘制颜色（与前端一致）
        # 直接使用get_cluster_color_mapping的逻辑来获取颜色映射，确保与前端完全一致
        table_name_plot = f"spot_cluster_{slice_id}"
        query_color = text(
            f"SELECT DISTINCT cluster FROM `{table_name_plot}` WHERE cluster_result_id = :cluster_result_id"
        )
        
        with engine.connect() as conn:
            rows_color = conn.execute(query_color, {"cluster_result_id": cluster_result_id}).fetchall()
        
        if not rows_color:
            print("⚠️ 没有找到cluster标签，跳过颜色映射")
            color_mapping = {}
        else:
            # 使用与get_cluster_color_mapping API完全相同的排序逻辑
            def sort_key(name: str):
                s = str(name)
                try:
                    return float(s)
                except ValueError:
                    return float("inf")
            
            clusters_for_color = sorted((str(r[0]) for r in rows_color), key=sort_key)
            color_mapping = build_cluster_color_mapping(clusters_for_color)

        clusters = df["cluster"].unique()
        for cluster in clusters:
            cluster_data = df[df["cluster"] == cluster]
            color_str = color_mapping.get(str(cluster), "rgb(148, 163, 184)")  # 默认灰色
            if color_str.startswith("rgb"):
                rgb_vals = [int(v.strip()) for v in color_str[4:-1].split(",")]
                color = tuple(v / 255.0 for v in rgb_vals)
            else:
                color = color_str

            ax.scatter(
                cluster_data["x"],
                cluster_data["y"],
                s=18,
                color=[color] if isinstance(color, tuple) else color,
                alpha=1.0,
                linewidths=0,
                edgecolors="none"
            )

        # 设置坐标范围和比例（数据已做 y 翻转，此处用正常 y 轴方向）
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min, y_max)
        ax.set_aspect("equal", adjustable="box")
        ax.axis("off")
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        
        # 保存图片
        plot_filename = f"{slice_id}_{cluster_result_id}_plot.png"
        plot_path = plots_dir / plot_filename
        plt.savefig(plot_path, dpi=150, bbox_inches='tight')
        plt.close()  # 关闭图形以释放内存
        
        print(f"✅ Plot 已保存到: {plot_path}")
        # 返回相对路径，格式：{slice_id}/plots/{filename}
        return f"{slice_id}/plots/{plot_filename}"
        
    except Exception as e:
        print(f"⚠️ 生成 plot 时出错: {e}")
        import traceback
        traceback.print_exc()
        return None
    
@app.get("/plot-data")
def get_plot_data(slice_id: str = Query(...), cluster_result_id: str = "default"):
    """
    获取 plot 数据，从数据库读取指定 slice_id 和 cluster_result_id 的聚类和坐标信息
    """
    global factor
    try:
        print(f"📊 获取 plot 数据: slice_id={slice_id}, cluster_result_id={cluster_result_id}")
        
        factor = get_scalefactor(slice_id)
        # 从数据库读取 cluster 和坐标信息
        table_name = f"spot_cluster_{slice_id}"
        
        # 检查表是否存在
        with engine.connect() as conn:
            # 检查表是否存在
            result = conn.execute(text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")).fetchone()
            if not result:
                error_msg = f"❌ 表 {table_name} 不存在。请先运行聚类算法或确保切片数据已正确加载。"
                print(error_msg)
                raise HTTPException(status_code=404, detail=error_msg)
            
            query = text(f"SELECT barcode, cluster, x, y FROM `{table_name}` WHERE cluster_result_id = :cluster_result_id")
            rows = conn.execute(query, {"cluster_result_id": cluster_result_id}).fetchall()
            
            print(f"📈 从数据库读取到 {len(rows)} 条记录 (slice_id={slice_id}, cluster_result_id={cluster_result_id})")

        # 构造 DataFrame
        if not rows:
            # 如果查询结果为空，尝试检查是否有其他 cluster_result_id 的数据
            with engine.connect() as conn:
                check_query = text(f"SELECT DISTINCT cluster_result_id FROM `{table_name}` LIMIT 5")
                available_ids = conn.execute(check_query).fetchall()
                available_ids_str = ", ".join([str(row[0]) for row in available_ids])
                warning_msg = f"⚠️ 未找到 cluster_result_id={cluster_result_id} 的数据。可用的 cluster_result_id: {available_ids_str if available_ids else '无'}"
                print(warning_msg)
                # 返回空列表而不是抛出异常，让前端处理
                return []

        df = pd.DataFrame(rows, columns=["barcode", "cluster", "x", "y"])
        # print("🔍 get_plot_data 中聚类分布：", df["cluster"].value_counts())
        
        df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["x", "y"])
        
        if df.empty:
            print(f"⚠️ 数据清理后为空 (slice_id={slice_id}, cluster_result_id={cluster_result_id})")
            return []

        # 应用缩放因子（与当前实际使用的 tissue 图一致：hires 或 lowres）
        df["x"] = df["x"] * factor
        df["y"] = df["y"] * factor

        img_size = _get_tissue_image_size(slice_id)
        if img_size is not None:
            img_w, img_h = img_size
            # 若 tissue_positions 为 10x 2.0 顺序 (row,col) 但被存成 (x,y)，缩放后宽高会与图反
            x_range = df["x"].max() - df["x"].min()
            y_range = df["y"].max() - df["y"].min()
            fit_xy = abs(x_range - img_w) + abs(y_range - img_h)
            fit_yx = abs(x_range - img_h) + abs(y_range - img_w)
            if fit_yx < fit_xy:
                df["x"], df["y"] = df["y"].copy(), df["x"].copy()
            # Visium 坐标 y 向下，Plotly y 向上；翻转 y 使点与底图对齐
            df["y"] = img_h - df["y"]

        # 构造 Plotly traces
        traces = []
        for cluster_id, group in df.groupby("cluster"):
            trace = {
                "x": group["x"].tolist(),
                "y": group["y"].tolist(),
                "name": cluster_id,
                "type": "scatter",
                "mode": "markers",
                "customdata": group["barcode"].tolist(),
                "hovertemplate": "Barcode: %{customdata}<extra></extra>",
            }
            traces.append(trace)

        print(f"✅ 成功生成 {len(traces)} 个 traces (slice_id={slice_id}, cluster_result_id={cluster_result_id})")
        return traces
        
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"❌ 获取 plot 数据失败 (slice_id={slice_id}, cluster_result_id={cluster_result_id}): {str(e)}"
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)



@app.post("/attention-flow-centers")
def get_attention_flow_centers(
    slice_id: str = Query(...),
    candidate_barcodes: list[str] = Body(None, description="Optional list of candidate barcodes to filter by")
):
    """
    获取可用的 center barcodes，用于极坐标图选择器
    如果提供了 candidate_barcodes，则只返回这些 barcodes 中存在的 centers
    否则返回所有 centers
    """
    global _attention_centers_cache
    
    try:
        csv_path = "./whole_slice_attention_layer_5.csv"
        if not os.path.exists(csv_path):
            print(f"⚠️ 文件不存在: {csv_path}")
            return {"centers": []}
        
        # Load cache if needed
        if _attention_centers_cache is None:
            print(f"📊 首次加载: 正在缓存所有 Attention Flow Centers...")
            start_time = time.time()
            chunk_size = 100000
            center_set = set()
            
            # 只读取 center_name 列
            for chunk in pd.read_csv(csv_path, usecols=['center_name'], chunksize=chunk_size):
                center_set.update(chunk['center_name'].unique())
            
            _attention_centers_cache = center_set
            print(f"✅ 缓存完成: {_attention_centers_cache and len(_attention_centers_cache)} 个 centers, 耗时 {time.time() - start_time:.2f}s")
        
        print(f"📊 获取 center barcodes (slice_id={slice_id})")
        if candidate_barcodes:
            print(f"  - 筛选候选 barcodes: {len(candidate_barcodes)} 个")
            # 使用内存缓存进行快速交集运算
            candidate_set = set(candidate_barcodes)
            valid_centers = list(_attention_centers_cache.intersection(candidate_set))
            centers_list = sorted(valid_centers)
        else:
            # 返回所有
            centers_list = sorted(list(_attention_centers_cache))
            
        print(f"✅ 找到 {len(centers_list)} 个可用的 center barcodes")
        
        return {"centers": centers_list}
        
    except Exception as e:
        error_msg = f"❌ 获取 center 列表失败 (slice_id={slice_id}): {str(e)}"
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/attention-flow-radial")
def get_attention_flow_radial(
    slice_id: str = Query(...),
    cluster_result_id: str = Query(None),
    center_barcodes: list[str] = Body(
        ..., description="List of center barcodes in the selected region"
    ),
):
    """
    获取选中区域的 attention flow 数据，用于绘制极坐标图
    返回每个 center 的 neighbors 及其 cluster 信息
    """
    try:
        if not center_barcodes or len(center_barcodes) == 0:
            return {"centers": []}
        
        csv_path = "./whole_slice_attention_layer_5.csv"
        if not os.path.exists(csv_path):
            print(f"⚠️ 文件不存在: {csv_path}")
            return {"centers": []}
        
        print(f"📊 开始处理极坐标图数据，选中 {len(center_barcodes)} 个 center barcodes")
        
        # 读取 attention 数据，只保留 center_name 在选中列表中的记录
        # 同时读取 kv_gene_symbol，用于后续代表基因统计
        usecols = ["center_name", "neighbor_name", "attn_score", "kv_gene_symbol"]
        chunk_size = 100000
        chunks = []
        
        center_barcodes_set = set(center_barcodes)
        
        for chunk in pd.read_csv(csv_path, usecols=usecols, chunksize=chunk_size):
            # 筛选出 center_name 在选中列表中的记录
            filtered_chunk = chunk[chunk['center_name'].isin(center_barcodes_set)]
            if len(filtered_chunk) > 0:
                chunks.append(filtered_chunk)
        
        if not chunks:
            print("⚠️ 未找到匹配的 attention 数据")
            return {"centers": []}
        
        df_attn = pd.concat(chunks, ignore_index=True)
        print(f"📊 找到 {len(df_attn)} 条匹配的 attention 记录")

        # Step 2: 获取 neighbor 的 cluster 信息（从 spot_cluster_{slice_id}）
        table_name = f"spot_cluster_{slice_id}"
        with engine.connect() as conn:
            # 如果前端传了 cluster_result_id，就直接用；否则自动选择一个可用的 ID（保持向后兼容）
            if cluster_result_id is not None:
                effective_cluster_result_id = cluster_result_id
            else:
                available_ids_query = text(
                    f"SELECT DISTINCT cluster_result_id FROM `{table_name}` ORDER BY cluster_result_id"
                )
                available_ids = conn.execute(available_ids_query).fetchall()
                effective_cluster_result_id = "default"
                if available_ids:
                    available_id_list = [row[0] for row in available_ids]
                    if "default" not in available_id_list:
                        effective_cluster_result_id = available_id_list[0]

            # ⚠️ 先把 CSV 里的 neighbor_name 全部标准化（去空格、转字符串）
            neighbor_barcodes = (
                df_attn["neighbor_name"].astype(str).str.strip().unique().tolist()
            )
            if len(neighbor_barcodes) == 0:
                print("⚠️ 没有可用的 neighbor barcodes")
                return {"centers": []}

            # 直接用字符串，不再强制转 int，保留原始 cluster 标签（例如 "N54"）
            neighbor_cluster_map: dict[str, str | None] = {}
            batch_size = 1000
            for i in range(0, len(neighbor_barcodes), batch_size):
                batch = neighbor_barcodes[i : i + batch_size]
                placeholders = ",".join([f":barcode_{j}" for j in range(len(batch))])
                query = text(
                    f"SELECT barcode, cluster FROM `{table_name}` "
                    f"WHERE cluster_result_id = :cluster_result_id AND barcode IN ({placeholders})"
                )
                params: dict[str, object] = {
                    "cluster_result_id": effective_cluster_result_id
                }
                for j, bc in enumerate(batch):
                    params[f"barcode_{j}"] = bc
                rows = conn.execute(query, params).fetchall()
                for row in rows:
                    # 数据库里读出来也做一次标准化
                    barcode_db = (
                        str(row[0]).strip()
                        if isinstance(row, tuple)
                        else str(row.barcode).strip()
                    )
                    cluster = row[1] if isinstance(row, tuple) else row.cluster
                    neighbor_cluster_map[barcode_db] = (
                        str(cluster).strip() if cluster is not None else None
                    )

        print(f"📊 从数据库获取到 {len(neighbor_cluster_map)} 个 neighbor 的 cluster")

        # Step 2+3: 按 center 分组，先统计邻居 cluster 的 attention 总和，再为每个 cluster 选代表基因（kv_gene_symbol）
        centers_data = []
        for center_barcode in center_barcodes:
            center_data = df_attn[df_attn["center_name"] == center_barcode].copy()
            if len(center_data) == 0:
                print(f"⚠️ center {center_barcode} 在 df_attn 中没有记录")
                continue

            # 标准化 neighbor_name 再映射，并丢弃没有 cluster 的邻居
            center_data["neighbor_clean"] = (
                center_data["neighbor_name"].astype(str).str.strip()
            )
            center_data["cluster"] = center_data["neighbor_clean"].map(
                neighbor_cluster_map
            )
            center_data["gene"] = center_data["kv_gene_symbol"].astype(str)

            # 丢弃没有 cluster 的邻居
            center_data = center_data[center_data["cluster"].notna()]
            if center_data.empty:
                continue

            # Step 2：按 cluster 聚合 attention 强度
            cluster_total = (
                center_data.groupby("cluster")["attn_score"]
                .sum()
                .reset_index(name="total_attn_score")
            )
            if cluster_total.empty:
                continue

            # Step 3：在每个 cluster 内按基因聚合，选 top K 基因
            gene_cluster_agg = (
                center_data.groupby(["cluster", "gene"])["attn_score"]
                .sum()
                .reset_index(name="gene_attn_score")
            )

            neighbors: list[dict[str, object]] = []
            TOP_GENES_PER_CLUSTER = 5

            # 只保留 attention 强度最高的若干个 cluster，避免图过乱
            cluster_total_sorted = cluster_total.sort_values(
                "total_attn_score", ascending=False
            ).head(20)

            for _, crow in cluster_total_sorted.iterrows():
                cluster_id = crow["cluster"]
                total_score = float(crow["total_attn_score"])

                # 该 cluster 下的所有基因，按贡献度排序
                genes_in_cluster = gene_cluster_agg[
                    gene_cluster_agg["cluster"] == cluster_id
                ].sort_values("gene_attn_score", ascending=False)

                top_genes_rows = genes_in_cluster.head(TOP_GENES_PER_CLUSTER)
                top_genes = [
                    {
                        "gene": str(row["gene"]),
                        "score": float(row["gene_attn_score"]),
                    }
                    for _, row in top_genes_rows.iterrows()
                ]

                neighbors.append(
                    {
                        # 保留原始 cluster 标签（可能是 "54" 或 "N54"）
                        "cluster": str(cluster_id),
                        "attn_score": total_score,
                        "top_genes": top_genes,
                    }
                )

            if neighbors:
                centers_data.append(
                    {
                        "center_barcode": str(center_barcode),
                        "neighbors": neighbors,
                    }
                )
        
        print(f"✅ 成功生成 {len(centers_data)} 个 center 的极坐标图数据")
        return {"centers": centers_data}
        
    except Exception as e:
        error_msg = f"❌ 获取极坐标图数据失败 (slice_id={slice_id}): {str(e)}"
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)

def _slice_data_ready(sid: str) -> tuple[bool, str]:
    """检查切片目录是否包含 Visium 所需文件（h5 或 matrix_counts.csv），返回 (是否就绪, 错误说明)。"""
    base = os.path.join("./data", sid)
    if not os.path.isdir(base):
        return False, "切片目录不存在"
    h5_path = os.path.join(base, "filtered_feature_bc_matrix.h5")
    matrix_csv = os.path.join(base, "spatial", "matrix_counts.csv")
    if not os.path.isfile(matrix_csv):
        matrix_csv = os.path.join(base, "matrix_counts.csv")
    if not os.path.isfile(h5_path) and not os.path.isfile(matrix_csv):
        return False, "切片数据不完整，缺少 filtered_feature_bc_matrix.h5 或 matrix_counts.csv，请先准备该切片的 Visium 数据"
    spatial_dir = os.path.join(base, "spatial")
    sf_path = os.path.join(spatial_dir, "scalefactors_json.json")
    if not os.path.isfile(sf_path):
        return False, "切片数据不完整，缺少 spatial/scalefactors_json.json"
    if not os.path.isfile(h5_path) and not os.path.isfile(os.path.join(spatial_dir, "tissue_positions_list.csv")):
        return False, "使用 matrix_counts.csv 时需同时提供 spatial/tissue_positions_list.csv"
    return True, ""


@app.get("/changeSlice")
def change_slice(sliceid: str = Query(...)):
    global slice_id, loaded_slice_id, factor

    print(f"🌀 请求切换切片为: {sliceid}")
    prev_slice_id = slice_id
    prev_loaded_slice_id = loaded_slice_id

    ready, reason = _slice_data_ready(sliceid)
    if not ready:
        print(f"❌ 切换切片跳过 (slice_id={sliceid}): {reason}")
        raise HTTPException(status_code=404, detail=reason)

    if sliceid == loaded_slice_id:
        print(f"⚠️ 当前切片已是 {sliceid}，仍执行强制刷新")

    slice_id = sliceid
    loaded_slice_id = None  # 强制 prepare_data 重新加载

    try:
        spatial_dir = os.path.join(f"./data/{slice_id}", "spatial")
        with open(os.path.join(spatial_dir, "scalefactors_json.json"), "r") as f:
            sf = json.load(f)

        scale_key = "tissue_hires_scalef" if scale == "hires" else "tissue_lowres_scalef"
        factor = sf[scale_key]

        print(f"🔁 加载 scalefactor 为: {scale_key} = {factor}")

        create_tables(slice_id)
        prepare_data(force_reload=True)

        print(f"✅ 切片切换完成: {slice_id}")
        return {"status": "ok", "slice_id": slice_id}
    except Exception as e:
        slice_id = prev_slice_id
        loaded_slice_id = prev_loaded_slice_id
        error_msg = f"❌ 切换切片失败 (slice_id={sliceid}): {str(e)}"
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/expression")
def get_expression(barcode: str):
    global adata
    adata_local = adata.copy()
    if barcode not in adata.obs_names:
        raise HTTPException(status_code=404, detail="Barcode not found")
    i = adata_local.obs_names.get_loc(barcode)
    gene_names = adata_local.var_names.tolist()
    expr = adata_local.X[i].toarray().flatten() if hasattr(adata_local.X, "toarray") else adata_local.X[i]
    return dict(zip(gene_names, map(float, expr)))

@app.get("/expression-by-cluster")
def get_expression_by_cluster(
    cluster: str = Query(...),
    cluster_field: str = "cluster",
    slice_id: str = Query(None),
    cluster_result_id: str = "default",
    top_n: int = 20  # 返回top N个marker genes
):
    """
    获取指定cluster的marker genes（相对于其他cluster特异性高表达的基因）
    及其在该cluster中的平均表达值
    """
    global adata
    adata_local = adata.copy()

    target_slice = slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")

    apply_cluster_labels(adata_local, target_slice, cluster_result_id, cluster_field)

    if cluster_field not in adata_local.obs:
        raise HTTPException(status_code=400, detail=f"Field '{cluster_field}' not found in .obs")

    if cluster not in adata_local.obs[cluster_field].astype(str).unique():
        raise HTTPException(status_code=404, detail=f"Cluster '{cluster}' not found")

    # 获取该 cluster 中的 spot 索引
    mask = adata_local.obs[cluster_field].astype(str) == cluster
    if mask.sum() == 0:
        raise HTTPException(status_code=404, detail=f"No spots found for cluster '{cluster}'")

    # 使用rank_genes_groups计算marker genes（相对于其他cluster特异性高表达的基因）
    sc.pp.normalize_total(adata_local, target_sum=1e4)
    sc.pp.log1p(adata_local)
    
    # 计算所有cluster的marker genes
    sc.tl.rank_genes_groups(adata_local, groupby=cluster_field, method="wilcoxon", n_genes=top_n)
    
    # 获取指定cluster的marker genes
    result = adata_local.uns["rank_genes_groups"]
    cluster_str = str(cluster)
    
    # 查找该cluster在结果中的索引
    if cluster_str not in result["names"].dtype.names:
        # 如果找不到，可能cluster名称不匹配，尝试查找最接近的
        cluster_names = list(result["names"].dtype.names)
        if not cluster_names:
            raise HTTPException(status_code=404, detail=f"No marker genes found for cluster '{cluster}'")
        # 使用第一个cluster（通常按名称排序）
        cluster_str = cluster_names[0]
        print(f"⚠️ Cluster '{cluster}' not found in rank_genes_groups results, using '{cluster_str}' instead")
    
    # 获取该cluster的top marker genes
    marker_genes = result["names"][cluster_str][:top_n]
    logfoldchanges = result["logfoldchanges"][cluster_str][:top_n]
    pvals = result["pvals_adj"][cluster_str][:top_n]
    
    # 计算这些marker genes在该cluster中的平均表达值和coverage（表达该基因的细胞百分比）
    X_cluster = adata_local[mask].X
    gene_names = adata_local.var_names.tolist()
    n_cells_in_cluster = mask.sum()
    
    result_list = []
    for i, gene in enumerate(marker_genes):
        if gene in gene_names:
            try:
                gene_idx = adata_local.var_names.get_loc(gene)
            except KeyError:
                continue
            gene_expr = X_cluster[:, gene_idx]
            # 处理稀疏矩阵和密集矩阵
            if hasattr(gene_expr, "toarray"):  # 稀疏矩阵 (SparseCSRView等)
                expr_array = gene_expr.toarray().flatten()
            elif hasattr(gene_expr, "A1"):  # 另一种稀疏矩阵格式
                expr_array = gene_expr.A1
            elif hasattr(gene_expr, "flatten"):  # 密集矩阵
                expr_array = gene_expr.flatten()
            elif hasattr(gene_expr, "ravel"):  # numpy数组
                expr_array = gene_expr.ravel()
            else:
                # 最后的fallback：转换为numpy数组
                expr_array = np.array(gene_expr).flatten()
            
            # 计算平均表达值
            mean_expr = float(expr_array.mean())
            
            # 计算coverage（表达该基因的细胞百分比）
            n_expressing = int((expr_array > 0).sum())
            coverage = float(n_expressing / n_cells_in_cluster) if n_cells_in_cluster > 0 else 0.0
            
            # 获取logFC
            logfc = float(logfoldchanges[i]) if i < len(logfoldchanges) else 0.0
            
            # 获取p值
            pval = float(pvals[i]) if i < len(pvals) else 1.0
            
            result_list.append({
                "gene": gene,
                "logFC": logfc,
                "avg_expr": mean_expr,
                "coverage": coverage,
                "pval_adj": pval
            })
    
    # 按logFC降序排序
    result_list.sort(key=lambda x: x["logFC"], reverse=True)
    
    return result_list


@app.get("/expression-by-all-clusters")
def get_expression_by_all_clusters(
    cluster_field: str = "cluster",
    slice_id: str = Query(None),
    cluster_result_id: str = "default",
    top_n: int = 20  # 返回top N个marker genes per cluster
):
    """
    一次性获取所有cluster的marker genes（相对于其他cluster特异性高表达的基因）
    返回格式: {cluster: [{gene, logFC, avg_expr, coverage, pval_adj}, ...], ...}
    """
    global adata
    adata_local = adata.copy()

    target_slice = slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")

    apply_cluster_labels(adata_local, target_slice, cluster_result_id, cluster_field)

    if cluster_field not in adata_local.obs:
        raise HTTPException(status_code=400, detail=f"Field '{cluster_field}' not found in .obs")

    # 获取所有cluster列表
    clusters = sorted(adata_local.obs[cluster_field].astype(str).unique())
    if not clusters:
        return {}

    # 使用rank_genes_groups计算所有cluster的marker genes（只计算一次）
    sc.pp.normalize_total(adata_local, target_sum=1e4)
    sc.pp.log1p(adata_local)
    
    # 计算所有cluster的marker genes
    sc.tl.rank_genes_groups(adata_local, groupby=cluster_field, method="wilcoxon", n_genes=top_n)
    
    result = adata_local.uns["rank_genes_groups"]
    gene_names = adata_local.var_names.tolist()
    
    # 为每个cluster准备结果
    all_results = {}
    
    for cluster_str in clusters:
        if cluster_str not in result["names"].dtype.names:
            continue
            
        # 获取该cluster的top marker genes
        marker_genes = result["names"][cluster_str][:top_n]
        logfoldchanges = result["logfoldchanges"][cluster_str][:top_n]
        pvals = result["pvals_adj"][cluster_str][:top_n]
        
        # 获取该 cluster 中的 spot 索引
        mask = adata_local.obs[cluster_field].astype(str) == cluster_str
        if mask.sum() == 0:
            continue
            
        X_cluster = adata_local[mask].X
        n_cells_in_cluster = mask.sum()
        
        result_list = []
        for i, gene in enumerate(marker_genes):
            if gene in gene_names:
                try:
                    gene_idx = adata_local.var_names.get_loc(gene)
                except KeyError:
                    continue
                gene_expr = X_cluster[:, gene_idx]
                # 处理稀疏矩阵和密集矩阵
                if hasattr(gene_expr, "toarray"):  # 稀疏矩阵 (SparseCSRView等)
                    expr_array = gene_expr.toarray().flatten()
                elif hasattr(gene_expr, "A1"):  # 另一种稀疏矩阵格式
                    expr_array = gene_expr.A1
                elif hasattr(gene_expr, "flatten"):  # 密集矩阵
                    expr_array = gene_expr.flatten()
                elif hasattr(gene_expr, "ravel"):  # numpy数组
                    expr_array = gene_expr.ravel()
                else:
                    # 最后的fallback：转换为numpy数组
                    expr_array = np.array(gene_expr).flatten()
                
                # 计算平均表达值（NaN/Inf 转为 None 以符合 JSON）
                mean_expr_raw = float(expr_array.mean())
                mean_expr = mean_expr_raw if np.isfinite(mean_expr_raw) else None

                n_expressing = int((expr_array > 0).sum())
                coverage_raw = float(n_expressing / n_cells_in_cluster) if n_cells_in_cluster > 0 else 0.0
                coverage = coverage_raw if np.isfinite(coverage_raw) else None

                logfc_raw = float(logfoldchanges[i]) if i < len(logfoldchanges) else 0.0
                logfc = logfc_raw if np.isfinite(logfc_raw) else None

                pval_raw = float(pvals[i]) if i < len(pvals) else 1.0
                pval = pval_raw if np.isfinite(pval_raw) else None

                result_list.append({
                    "gene": gene,
                    "logFC": logfc,
                    "avg_expr": mean_expr,
                    "coverage": coverage,
                    "pval_adj": pval
                })
        
        # 按logFC降序排序（logFC 为 None 的排到最后）
        result_list.sort(key=lambda x: (x["logFC"] if x["logFC"] is not None else -float("inf")), reverse=True)
        all_results[cluster_str] = result_list
    
    return all_results


# Cache for cell type marker gene sets from Enrichr
_CELL_TYPE_MARKERS_CACHE = None


def _fetch_cell_type_markers_from_enrichr():
    """
    从 Enrichr 获取所有细胞类型的 marker gene sets（正向匹配方法）
    返回格式: {cell_type_name: {"genes": [gene_list]}, ...}
    """
    global _CELL_TYPE_MARKERS_CACHE
    
    if _CELL_TYPE_MARKERS_CACHE is not None:
        return _CELL_TYPE_MARKERS_CACHE
    
    import requests
    
    try:
        # 获取可用的库
        available_libs = gp.get_library_name()
        
        # 查找细胞类型相关的库（按优先级）
        preferred_libs = [
            "PanglaoDB_Augmented_2021",
            "PanglaoDB_2019",
            "CellMarker_Augmented_2021",
            "CellMarker_2021",
        ]
        
        library_name = None
        for lib in preferred_libs:
            if lib in available_libs:
                library_name = lib
                break
        
        if not library_name:
            # 搜索包含关键词的库
            cell_type_libs = [
                lib for lib in available_libs 
                if any(keyword in lib.lower() for keyword in ["panglao", "cellmarker"])
            ]
            if cell_type_libs:
                library_name = cell_type_libs[0]
        
        if not library_name:
            print("⚠️ No cell type marker library found in Enrichr, using fallback")
            _CELL_TYPE_MARKERS_CACHE = _get_fallback_markers()
            return _CELL_TYPE_MARKERS_CACHE
        
        print(f"📚 Fetching cell type markers from: {library_name}")
        
        # 从 Enrichr API 获取 gene sets
        # API endpoint: https://maayanlab.cloud/Enrichr/geneSetLibrary
        enrichr_url = "https://maayanlab.cloud/Enrichr/geneSetLibrary"
        params = {
            "mode": "text",
            "libraryName": library_name
        }
        
        response = requests.get(enrichr_url, params=params, timeout=30)
        if response.status_code == 200:
            cell_type_markers = {}
            lines = response.text.strip().split('\n')
            
            for line in lines:
                if not line.strip():
                    continue
                parts = line.split('\t')
                if len(parts) >= 2:
                    cell_type_name = parts[0].strip()
                    # 基因列表可能在第二列或更多列，通常是逗号分隔
                    genes_str = parts[1] if len(parts) == 2 else '\t'.join(parts[1:])
                    # 处理不同的分隔符：可能是逗号、分号或制表符
                    genes = []
                    for sep in [',', ';', '\t']:
                        if sep in genes_str:
                            genes = [g.strip().upper() for g in genes_str.split(sep) if g.strip()]
                            break
                    if not genes:
                        genes = [genes_str.strip().upper()]
                    
                    if cell_type_name and genes:
                        cell_type_markers[cell_type_name] = {"genes": genes}
            
            print(f"✅ Loaded {len(cell_type_markers)} cell types from {library_name}")
            _CELL_TYPE_MARKERS_CACHE = cell_type_markers
            return cell_type_markers
        else:
            print(f"⚠️ Failed to fetch from Enrichr (status {response.status_code}), using fallback")
            _CELL_TYPE_MARKERS_CACHE = _get_fallback_markers()
            return _CELL_TYPE_MARKERS_CACHE
            
    except Exception as e:
        print(f"⚠️ Error fetching cell type markers from Enrichr: {str(e)}, using fallback")
        import traceback
        traceback.print_exc()
        _CELL_TYPE_MARKERS_CACHE = _get_fallback_markers()
        return _CELL_TYPE_MARKERS_CACHE


def _get_fallback_markers():
    """Fallback marker genes if Enrichr is unavailable"""
    return {
        "Neurons": {
            "genes": ["MAP2", "TUBB3", "RBFOX3", "SYN1", "SNAP25", "STX1A", "GAD1", "GAD2"],
        },
        "Astrocytes": {
            "genes": ["GFAP", "S100B", "AQP4", "GJA1", "ALDH1L1", "SLC1A3"],
        },
        "Oligodendrocytes": {
            "genes": ["OLIG2", "MBP", "MOG", "PLP1", "CNP", "MOBP"],
        },
        "Microglia": {
            "genes": ["P2RY12", "TMEM119", "CSF1R", "CX3CR1", "AIF1", "CD68"],
        },
        "Endothelial cells": {
            "genes": ["PECAM1", "CDH5", "VWF", "ENG", "KDR", "FLT1"],
        },
        "T cells": {
            "genes": ["CD3D", "CD3E", "CD3G", "CD2", "CD5", "CD7"],
        },
        "B cells": {
            "genes": ["CD19", "CD79A", "CD79B", "MS4A1", "CD22", "PAX5"],
        },
        "Macrophages": {
            "genes": ["CD68", "CD163", "MRC1", "MSR1", "FCGR1A", "CD14"],
        },
    }


@app.get("/annotate-cell-types")
def annotate_cell_types(
    slice_id: str = Query(None),
    cluster_result_id: str = "default",
    top_n: int = 20,
    min_match_ratio: float = 0.15  # 至少匹配15%的marker genes
):
    """
    基于marker genes为每个cluster注释细胞类型
    返回格式: {cluster: {"cell_type": "predicted_type", "score": match_score, "matched_genes": [...]}, ...}
    """
    global adata
    adata_local = adata.copy()

    target_slice = slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")

    # 获取所有cluster的marker genes
    apply_cluster_labels(adata_local, target_slice, cluster_result_id, "cluster")

    # 获取marker genes（复用现有的接口逻辑）
    clusters = sorted(adata_local.obs["cluster"].astype(str).unique())
    if not clusters:
        return {}

    sc.pp.normalize_total(adata_local, target_sum=1e4)
    sc.pp.log1p(adata_local)
    sc.tl.rank_genes_groups(adata_local, groupby="cluster", method="wilcoxon", n_genes=top_n)

    result = adata_local.uns["rank_genes_groups"]
    
    # 从 Enrichr 获取所有细胞类型的 marker gene sets（正向匹配）
    cell_type_markers = _fetch_cell_type_markers_from_enrichr()
    print(f"📊 Loaded {len(cell_type_markers)} cell type marker sets")
    
    # 为每个cluster注释细胞类型
    annotations = {}
    
    for cluster_str in clusters:
        if cluster_str not in result["names"].dtype.names:
            continue
        
        # 获取该cluster的top marker genes（转换为大写）
        marker_genes = [g.upper() for g in result["names"][cluster_str][:top_n].tolist()]
        marker_genes_set = set(marker_genes)
        
        best_match = None
        best_score = 0
        best_matched_genes = []
        
        # 与每个已知细胞类型的marker genes进行比较（正向匹配）
        for cell_type_name, cell_info in cell_type_markers.items():
            known_markers = [g.upper() for g in cell_info["genes"]]
            known_markers_set = set(known_markers)
            
            # 计算重叠
            intersection = marker_genes_set & known_markers_set
            
            if len(intersection) > 0:
                # 计算多个得分指标
                # 1. Jaccard 相似度：intersection / union
                union = marker_genes_set | known_markers_set
                jaccard_score = len(intersection) / len(union) if len(union) > 0 else 0
                
                # 2. 重叠比例（相对于已知marker genes）
                overlap_ratio = len(intersection) / len(known_markers_set) if len(known_markers_set) > 0 else 0
                
                # 3. 重叠比例（相对于cluster的marker genes）
                cluster_overlap_ratio = len(intersection) / len(marker_genes_set) if len(marker_genes_set) > 0 else 0
                
                # 综合得分：考虑多个因素
                # - Jaccard 相似度：衡量整体相似度（权重 0.5）
                # - 重叠比例（相对于已知markers）：衡量匹配的完整性（权重 0.3）
                # - 重叠比例（相对于cluster markers）：衡量cluster marker genes的匹配度（权重 0.2）
                combined_score = (
                    jaccard_score * 0.5 +
                    overlap_ratio * 0.3 +
                    cluster_overlap_ratio * 0.2
                )
                
                # 至少匹配 min_match_ratio 比例的已知marker genes
                # 或者至少匹配2个基因（降低阈值以提高匹配率）
                min_overlap_required = max(min_match_ratio, 2.0 / len(known_markers_set) if len(known_markers_set) > 0 else 1.0)
                if combined_score > best_score and (overlap_ratio >= min_match_ratio or len(intersection) >= 2):
                    best_score = combined_score
                    best_match = cell_type_name
                    best_matched_genes = list(intersection)
        
        if best_match:
            known_marker_count = len(cell_type_markers[best_match]["genes"])
            annotations[cluster_str] = {
                "cell_type": best_match,
                "score": round(best_score, 3),
                "matched_genes": best_matched_genes[:10],  # 最多返回10个匹配的基因
                "match_ratio": round(len(best_matched_genes) / known_marker_count, 3) if known_marker_count > 0 else 0.0
            }
            print(f"✅ Cluster {cluster_str}: {best_match} (score: {best_score:.3f}, matched: {len(best_matched_genes)} genes)")
        else:
            annotations[cluster_str] = {
                "cell_type": "Unknown",
                "score": 0.0,
                "matched_genes": [],
                "match_ratio": 0.0
            }
            print(f"⚠️ Cluster {cluster_str}: No match found (top marker genes: {marker_genes[:5]})")
    
    print(f"📋 Annotated {len(annotations)} clusters")
    return annotations


class LassoStatisticsRequest(BaseModel):
    barcodes: List[str]
    slice_id: Optional[str] = None
    cluster_result_id: str = "default"

@app.post("/lasso-statistics")
def get_lasso_statistics(req: LassoStatisticsRequest):
    """
    Get statistical analysis for lasso-selected spots to help determine cluster assignment
    Returns cluster distribution, similarity scores, and recommended cluster
    """
    import numpy as np
    from scipy.spatial.distance import cosine
    from collections import Counter
    global adata
    
    target_slice = req.slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")
    
    # Parse barcodes
    barcode_list = [b.strip() for b in req.barcodes if b.strip()]
    if not barcode_list:
        raise HTTPException(status_code=400, detail="No valid barcodes provided")
    
    # Apply cluster labels
    adata_local = adata.copy()
    apply_cluster_labels(adata_local, target_slice, req.cluster_result_id, "domain")
    
    # Filter to valid barcodes
    valid_barcodes = [b for b in barcode_list if b in adata_local.obs_names]
    if not valid_barcodes:
        raise HTTPException(status_code=404, detail="No matching barcodes found")
    
    # Get cluster field
    cluster_field = "domain" if "domain" in adata_local.obs.columns else "cluster"
    if cluster_field not in adata_local.obs.columns:
        raise HTTPException(status_code=400, detail="No cluster field found")
    
    # 1. Cluster distribution of selected spots
    selected_clusters = adata_local.obs.loc[valid_barcodes, cluster_field].tolist()
    cluster_counts = Counter(selected_clusters)
    cluster_distribution = [
        {"cluster": str(c), "count": cnt, "percentage": round(cnt / len(valid_barcodes) * 100, 1)}
        for c, cnt in sorted(cluster_counts.items(), key=lambda x: -x[1])
    ]
    
    # 2. Calculate average expression profile of selected spots
    selected_indices = [adata_local.obs_names.get_loc(b) for b in valid_barcodes]
    if hasattr(adata_local.X, 'toarray'):
        selected_expr = np.array(adata_local.X[selected_indices, :].toarray())
    else:
        selected_expr = np.array(adata_local.X[selected_indices, :])
    avg_selected_expr = np.mean(selected_expr, axis=0).flatten()
    
    # 3. Calculate cluster centroids and similarity scores
    all_clusters = sorted(adata_local.obs[cluster_field].unique())
    cluster_similarities = []
    
    for cluster in all_clusters:
        cluster_mask = adata_local.obs[cluster_field] == cluster
        cluster_indices = np.where(cluster_mask)[0]
        
        if len(cluster_indices) == 0:
            continue
        
        if hasattr(adata_local.X, 'toarray'):
            cluster_expr = np.array(adata_local.X[cluster_indices, :].toarray())
        else:
            cluster_expr = np.array(adata_local.X[cluster_indices, :])
        avg_cluster_expr = np.mean(cluster_expr, axis=0).flatten()
        
        # Calculate cosine similarity (1 - cosine distance)
        if np.linalg.norm(avg_selected_expr) > 0 and np.linalg.norm(avg_cluster_expr) > 0:
            similarity = 1 - cosine(avg_selected_expr, avg_cluster_expr)
        else:
            similarity = 0
        
        cluster_similarities.append({
            "cluster": str(cluster),
            "similarity": round(float(similarity), 4),
            "spot_count": int(cluster_mask.sum())
        })
    
    # Sort by similarity descending
    cluster_similarities.sort(key=lambda x: -x["similarity"])
    
    # 4. Find top expressed genes in selected spots
    gene_names = adata_local.var_names.tolist()
    gene_avg_expr = list(zip(gene_names, avg_selected_expr))
    gene_avg_expr.sort(key=lambda x: -x[1])
    top_genes = [
        {"gene": g, "avg_expression": round(float(e), 3)}
        for g, e in gene_avg_expr[:10]
    ]
    
    # 5. Recommended cluster (highest similarity, excluding clusters that already dominate the selection)
    recommended = None
    for sim in cluster_similarities:
        cluster_name = sim["cluster"]
        # Check if this cluster already has majority of selected spots
        current_count = cluster_counts.get(cluster_name, 0)
        if current_count < len(valid_barcodes) * 0.8:  # Less than 80% already in this cluster
            recommended = cluster_name
            break
    
    if recommended is None and cluster_similarities:
        recommended = cluster_similarities[0]["cluster"]
    
    return {
        "total_selected": len(valid_barcodes),
        "valid_barcodes": len(valid_barcodes),
        "cluster_distribution": cluster_distribution,
        "cluster_similarities": cluster_similarities[:10],  # Top 10
        "top_expressed_genes": top_genes,
        "recommended_cluster": recommended,
        "recommendation_confidence": cluster_similarities[0]["similarity"] if cluster_similarities else 0
    }


class SpotsClusterHistoryRequest(BaseModel):
    barcodes: List[str]
    slice_id: Optional[str] = None
    current_cluster_result_id: str = "default"

@app.post("/spots-cluster-history")
def get_spots_cluster_history(req: SpotsClusterHistoryRequest):
    """
    Get cluster assignments for spots across all clustering results (except current one)
    Returns a map of barcode -> list of {cluster_result_id, method, cluster}
    """
    from collections import defaultdict
    
    target_slice = req.slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")
    
    # Parse barcodes
    barcode_list = [b.strip() for b in req.barcodes if b.strip()]
    if not barcode_list:
        raise HTTPException(status_code=400, detail="No valid barcodes provided")
    
    metadata = MetaData()
    metadata.reflect(bind=engine)
    
    # Get cluster_method table to map cluster_result_id to method
    cluster_method_table = metadata.tables.get("cluster_method")
    method_map = {}
    
    if cluster_method_table is not None:
        with engine.connect() as conn:
            stmt = select(
                cluster_method_table.c.cluster_result_id,
                cluster_method_table.c.method
            ).where(
                cluster_method_table.c.slice_id == target_slice
            )
            results = conn.execute(stmt).fetchall()
            for row in results:
                method_map[row[0]] = row[1]
    
    # Get spot_cluster table
    spot_table_name = f"spot_cluster_{target_slice}"
    spot_table = metadata.tables.get(spot_table_name)
    
    if spot_table is None:
        return {"history": {}}
    
    # Build history for each barcode
    barcode_history = defaultdict(list)
    
    try:
        with engine.connect() as conn:
            # Query all cluster assignments for the given barcodes
            stmt = select(
                spot_table.c.barcode,
                spot_table.c.cluster_result_id,
                spot_table.c.cluster
            ).where(
                spot_table.c.barcode.in_(barcode_list),
                spot_table.c.cluster_result_id != req.current_cluster_result_id
            )
            spot_results = conn.execute(stmt).fetchall()
            
            for spot_row in spot_results:
                barcode = spot_row[0]
                result_id = spot_row[1]
                cluster = spot_row[2]
                
                if cluster is not None and result_id is not None:
                    barcode_history[barcode].append({
                        "result_id": result_id,
                        "method": method_map.get(result_id, "unknown"),
                        "cluster": str(cluster)
                    })
    except Exception as e:
        print(f"Error getting cluster history: {e}")
        import traceback
        traceback.print_exc()
    
    return {"history": dict(barcode_history)}


@app.get("/spot-gene-expression")
def get_spot_gene_expression(
    barcode: str = Query(..., description="The barcode of the spot"),
    slice_id: str = Query(None),
    top_n: int = Query(20, description="Number of top expressed genes to return")
):
    """
    Get gene expression data for a specific spot/barcode
    Returns top N most highly expressed genes for the spot
    """
    import numpy as np
    global adata
    
    target_slice = slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")
    
    # Find the barcode in adata
    barcode = barcode.strip()
    if barcode not in adata.obs_names:
        raise HTTPException(status_code=404, detail=f"Barcode '{barcode}' not found")
    
    # Get expression data for this spot
    spot_idx = adata.obs_names.get_loc(barcode)
    
    # Get the expression values (handle sparse matrix)
    if hasattr(adata.X, 'toarray'):
        expression_values = np.array(adata.X[spot_idx, :].toarray()).flatten()
    else:
        expression_values = np.array(adata.X[spot_idx, :]).flatten()
    
    # Get gene names
    gene_names = adata.var_names.tolist()
    
    # Create list of (gene, expression) tuples and sort by expression
    gene_expr_list = list(zip(gene_names, expression_values))
    gene_expr_list.sort(key=lambda x: x[1], reverse=True)
    
    # Get top N genes
    top_genes = gene_expr_list[:top_n]
    
    # Get spot coordinates if available
    x_coord = None
    y_coord = None
    if 'x' in adata.obs.columns:
        x_coord = float(adata.obs.loc[barcode, 'x'])
    if 'y' in adata.obs.columns:
        y_coord = float(adata.obs.loc[barcode, 'y'])
    
    # Get cluster info if available
    cluster = None
    if 'domain' in adata.obs.columns:
        cluster = str(adata.obs.loc[barcode, 'domain'])
    elif 'cluster' in adata.obs.columns:
        cluster = str(adata.obs.loc[barcode, 'cluster'])
    
    return {
        "barcode": barcode,
        "x": x_coord,
        "y": y_coord,
        "cluster": cluster,
        "total_genes": len(gene_names),
        "total_expression": float(expression_values.sum()),
        "top_genes": [
            {"gene": gene, "expression": float(expr)} 
            for gene, expr in top_genes
        ]
    }


@app.get("/cluster-gene-expression")
def get_cluster_gene_expression(
    cluster_field: str = "cluster",
    top_n: int = 5,
    slice_id: str = Query(None),
    cluster_result_id: str = "default",
    cluster_overrides: str = Query(None, description="Optional JSON dict of barcode->new_cluster for demo overrides"),
):
    import numpy as np
    from scipy.stats import zscore
    global adata
    adata_local = adata.copy()

    target_slice = slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")

    overrides = json.loads(cluster_overrides) if cluster_overrides else None
    apply_cluster_labels(adata_local, target_slice, cluster_result_id, cluster_field, overrides)

    # 如果指定的字段不存在，尝试使用 "domain" 或 "cluster"
    if cluster_field not in adata_local.obs:
        if "domain" in adata_local.obs.columns:
            cluster_field = "domain"
        elif "cluster" in adata_local.obs.columns:
            cluster_field = "cluster"
        else:
            raise HTTPException(status_code=400, detail=f"Neither 'cluster' nor 'domain' field found in .obs")
    
    sc.pp.normalize_total(adata_local, target_sum=1e4)
    sc.pp.log1p(adata_local)
    sc.tl.rank_genes_groups(adata_local, groupby=cluster_field, method="wilcoxon", n_genes=top_n)

    result = adata_local.uns["rank_genes_groups"]
    gene_list = []
    for group in result["names"].dtype.names:
        names = result["names"][group][:top_n]
        gene_list.extend(names)
    gene_list = list(dict.fromkeys(gene_list))

    # 按 cluster 分组排序 barcodes
    clusters_series = adata_local.obs[cluster_field].astype(str)
    barcodes = adata_local.obs_names.tolist()
    cluster_barcode_pairs = sorted(zip(clusters_series, barcodes), key=lambda x: (x[0], x[1]))
    sorted_clusters, sorted_barcodes = zip(*cluster_barcode_pairs)

    # 取表达值
    gene_idx = [adata_local.var_names.get_loc(g) for g in gene_list]
    X = adata_local[:, gene_idx].X
    X_dense = X.toarray() if hasattr(X, "toarray") else X
    # 按 barcodes 顺序重排
    barcode_idx = [adata_local.obs_names.get_loc(bc) for bc in sorted_barcodes]
    X_dense = X_dense[barcode_idx, :].T  # gene x cell

    # 按基因做z-score
    X_scaled = zscore(X_dense, axis=1, nan_policy='omit')
    X_scaled = np.nan_to_num(X_scaled, nan=0.0, posinf=0.0, neginf=0.0)

    return {
        "genes": gene_list,
        "barcodes": list(sorted_barcodes),
        "clusters": list(sorted_clusters),
        "expression": X_scaled.tolist()  # gene x cell
    }
    
@app.get("/cluster_gene_dotplot")
def get_dotplot_data(
    cluster_field: str = "cluster",
    top_n: int = 30,
    method: str = "hvg",  # or 'marker'
    slice_id: str = Query(None),
    cluster_result_id: str = "default",
    cluster_overrides: str = Query(None, description="Optional JSON dict of barcode->new_cluster for demo overrides"),
):
    global adata
    adata_local = adata.copy()
    adata_local.var_names_make_unique()

    target_slice = slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")

    overrides = json.loads(cluster_overrides) if cluster_overrides else None
    apply_cluster_labels(adata_local, target_slice, cluster_result_id, cluster_field, overrides)

    # 检查 cluster_field 是否存在，如果不存在则尝试使用 "domain"
    if cluster_field not in adata_local.obs.columns:
        if "domain" in adata_local.obs.columns:
            cluster_field = "domain"
            print(f"⚠️ 字段 'cluster' 不存在，使用 'domain' 代替")
        elif "cluster" in adata_local.obs.columns:
            cluster_field = "cluster"
        else:
            raise HTTPException(status_code=400, detail=f"Neither 'cluster' nor 'domain' field found in adata.obs")

    # Normalize and log
    sc.pp.normalize_total(adata_local, target_sum=1e4)
    sc.pp.log1p(adata_local)

    # ==== 🔧 添加数据检查和修复 ====
    if hasattr(adata_local.X, "toarray"):
        adata_local.X = adata_local.X.toarray()

    adata_local.X = np.nan_to_num(adata_local.X).astype(np.float32)
    # =================================

    # 选 gene 列表
    if method == "hvg":
        sc.pp.highly_variable_genes(adata_local, n_top_genes=top_n)
        gene_list = adata_local.var_names[adata_local.var["highly_variable"]].tolist()

    elif method == "marker":
        sc.tl.rank_genes_groups(adata_local, groupby=cluster_field, method="t-test", n_genes=top_n)
        top_genes = set()
        for group in adata_local.uns["rank_genes_groups"]["names"].dtype.names:
            top_genes.update(adata_local.uns["rank_genes_groups"]["names"][group][:top_n])
        gene_list = list(top_genes)

    else:
        raise HTTPException(status_code=400, detail="Invalid method")

    clusters = adata_local.obs[cluster_field].astype(str).tolist()
    
    # Sort helper function to handle mixed int/str types safely
    def sort_key(x):
        if x.isdigit():
            return (0, int(x), x)
        try:
            return (0, float(x), x)
        except ValueError:
            return (1, 0, x)
            
    cluster_names = sorted(set(clusters), key=sort_key)

    result = []

    for gene in gene_list:
        if gene not in adata_local.var_names:
            continue
        gene_idx = adata_local.var_names.get_loc(gene)
        expr = adata_local.X[:, gene_idx].flatten()

        for cluster in cluster_names:
            mask = (adata_local.obs[cluster_field].astype(str) == cluster).values
            expr_cluster = expr[mask]
            avg_expr = float(np.mean(expr_cluster))
            pct_expr = float(np.mean(expr_cluster > 0))
            result.append({
                "gene": gene,
                "cluster": cluster,
                "avg_expr": avg_expr,
                "pct_expr": pct_expr
            })

    return {
        "genes": gene_list,
        "clusters": cluster_names,
        "data": result
    }
      
@app.get("/slice-info")
def get_slice_info(slice_id: str = Query(..., description="Slide ID like 151673")):
    path = f"./data/{slice_id}"
    info_path = os.path.join(path, "info.json")

    if not os.path.exists(info_path):
        raise HTTPException(status_code=404, detail="info.json not found for this slice")

    # 读取 info.json 中的原始信息
    with open(info_path, "r") as f:
        info = json.load(f)

    # 加载 adata 并计算统计信息
    adata_local = sq.read.visium(path=path)
    sc.pp.filter_genes(adata_local, min_cells=3)
    sc.pp.normalize_total(adata_local)
    sc.pp.log1p(adata_local)

    # 添加基础统计信息
    info.update({
        "spot_count": adata_local.n_obs,
        "gene_count": adata_local.n_vars,
        "avg_genes_per_spot": round(float((adata_local.X > 0).sum(1).mean()), 2)
    })

    # 查询聚类方法表 - 返回所有聚类结果
    metadata = MetaData()
    metadata.reflect(bind=engine)
    cluster_method_table = metadata.tables["cluster_method"]

    with engine.connect() as conn:
        stmt = select(
            cluster_method_table.c.cluster_result_id,
            cluster_method_table.c.result_name,
            cluster_method_table.c.method,
            cluster_method_table.c.n_clusters,
            cluster_method_table.c.epoch
        ).where(cluster_method_table.c.slice_id == slice_id)
        results = conn.execute(stmt).fetchall()

    # 准备聚类方法列表
    cluster_results = []
    for r in results:
        cluster_results.append({
            "cluster_result_id": r.cluster_result_id,
            "result_name": r.result_name or f"{r.method}_{r.n_clusters}clusters",
            "method": r.method,
            "n_clusters": r.n_clusters,
            "epoch": r.epoch
        })

    # 把其余所有 info 字段打包
    return {
        "cluster_results": cluster_results,  # 返回所有聚类结果列表
        "info_details": info
    }

@app.get("/cluster-results")
def get_cluster_results(slice_id: str = Query(...)):
    """
    获取某个切片的所有聚类结果列表，包含 plot 路径
    """
    metadata = MetaData()
    metadata.reflect(bind=engine)
    cluster_method_table = metadata.tables["cluster_method"]

    with engine.connect() as conn:
        # 检查 plot_path 列是否存在
        result_cols = conn.execute(text("PRAGMA table_info(cluster_method)")).fetchall()
        existing_cols = [col[1] for col in result_cols]
        has_plot_path = "plot_path" in existing_cols
        
        # 检查评估指标列是否存在
        has_metrics = all(col in existing_cols for col in ["chao", "silhouette", "pas", "morans_i"])
        
        # 构建查询，如果列不存在则使用 None
        if has_plot_path and has_metrics:
            stmt = select(
                cluster_method_table.c.cluster_result_id,
                cluster_method_table.c.result_name,
                cluster_method_table.c.method,
                cluster_method_table.c.n_clusters,
                cluster_method_table.c.epoch,
                cluster_method_table.c.plot_path,
                cluster_method_table.c.chao,
                cluster_method_table.c.silhouette,
                cluster_method_table.c.pas,
                cluster_method_table.c.morans_i,
                cluster_method_table.c.updated_at
            ).where(cluster_method_table.c.slice_id == slice_id).order_by(cluster_method_table.c.updated_at.desc())
        else:
            # 如果列不存在，使用 text 查询
            stmt = text("""
                SELECT cluster_result_id, result_name, method, n_clusters, epoch, 
                       plot_path, chao, silhouette, pas, morans_i, updated_at
                FROM cluster_method
                WHERE slice_id = :slice_id
                ORDER BY updated_at DESC
            """)
        
        results = conn.execute(stmt, {"slice_id": slice_id} if not has_plot_path or not has_metrics else {}).fetchall()

    cluster_results = []
    for r in results:
        plot_path = r.plot_path if hasattr(r, 'plot_path') else (r[5] if len(r) > 5 else None)
        cluster_result_id = r.cluster_result_id if hasattr(r, 'cluster_result_id') else r[0]
        
        # 提取评估指标
        chao = r.chao if hasattr(r, 'chao') else (r[6] if len(r) > 6 else None)
        silhouette = r.silhouette if hasattr(r, 'silhouette') else (r[7] if len(r) > 7 else None)
        pas = r.pas if hasattr(r, 'pas') else (r[8] if len(r) > 8 else None)
        morans_i = r.morans_i if hasattr(r, 'morans_i') else (r[9] if len(r) > 9 else None)

        needs_regen = False
        if not plot_path:
            needs_regen = True
        else:
            plot_file = Path("data") / plot_path
            if not plot_file.exists():
                needs_regen = True

        if needs_regen:
            regenerated_path = save_cluster_plot(slice_id, cluster_result_id)
            if regenerated_path:
                plot_path = regenerated_path
                with engine.begin() as conn:
                    conn.execute(
                        update(cluster_method_table)
                        .where(
                            cluster_method_table.c.slice_id == slice_id,
                            cluster_method_table.c.cluster_result_id == cluster_result_id
                        )
                        .values(plot_path=plot_path)
                    )
        
        plot_url = None
        if plot_path:
            # Add timestamp to URL to prevent browser caching of old images
            updated_at = (r.updated_at if hasattr(r, 'updated_at') else (r[6] if len(r) > 6 else None))
            if updated_at:
                # Convert to timestamp for cache busting
                if isinstance(updated_at, str):
                    try:
                        updated_at_dt = parse(updated_at)
                        timestamp = int(updated_at_dt.timestamp())
                    except:
                        timestamp = int(datetime.now().timestamp())
                else:
                    timestamp = int(updated_at.timestamp()) if hasattr(updated_at, 'timestamp') else int(datetime.now().timestamp())
            else:
                timestamp = int(datetime.now().timestamp())
            plot_url = f"/images/{slice_id}/plots/{Path(plot_path).name}?t={timestamp}"
        else:
            plot_url = None
        
        cluster_results.append({
            "cluster_result_id": cluster_result_id,
            "result_name": (r.result_name if hasattr(r, 'result_name') else r[1]) or f"{(r.method if hasattr(r, 'method') else r[2])}_{(r.n_clusters if hasattr(r, 'n_clusters') else r[3])}clusters",
            "method": r.method if hasattr(r, 'method') else r[2],
            "n_clusters": r.n_clusters if hasattr(r, 'n_clusters') else r[3],
            "epoch": r.epoch if hasattr(r, 'epoch') else r[4],
            "plot_path": plot_path,
            "plot_url": plot_url,  # 前端可以直接使用的 URL (with cache-busting timestamp)
            "updated_at": (r.updated_at.isoformat() if r.updated_at else None) if hasattr(r, 'updated_at') else (r[10].isoformat() if len(r) > 10 and r[10] else None),
            # 聚类评估指标
            "metrics": {
                "chao": sanitize_float_for_json(chao),
                "silhouette": sanitize_float_for_json(silhouette),
                "pas": sanitize_float_for_json(pas),
                "morans_i": sanitize_float_for_json(morans_i)
            }
        })

    return cluster_results

@app.post("/regenerate-cluster-plots")
def regenerate_cluster_plots(slice_id: str = Query(..., description="Slice ID to regenerate plots for")):
    """
    强制重新生成指定切片的所有聚类结果的缩略图
    用于在删除或修改聚类结果后更新缩略图
    """
    try:
        metadata = MetaData()
        metadata.reflect(bind=engine)
        cluster_method_table = metadata.tables["cluster_method"]
        
        # 获取该切片的所有聚类结果
        with engine.connect() as conn:
            stmt = select(cluster_method_table.c.cluster_result_id).where(
                cluster_method_table.c.slice_id == slice_id
            )
            results = conn.execute(stmt).fetchall()
        
        regenerated_count = 0
        failed_count = 0
        results_list = []
        
        for r in results:
            cluster_result_id = r.cluster_result_id if hasattr(r, 'cluster_result_id') else r[0]
            
            try:
                # 强制重新生成缩略图
                plot_path = save_cluster_plot(slice_id, cluster_result_id)
                
                if plot_path:
                    # 更新数据库中的plot_path和updated_at（更新时间戳以刷新缓存）
                    with engine.begin() as conn:
                        conn.execute(
                            update(cluster_method_table)
                            .where(
                                cluster_method_table.c.slice_id == slice_id,
                                cluster_method_table.c.cluster_result_id == cluster_result_id
                            )
                            .values(plot_path=plot_path, updated_at=func.current_timestamp())
                        )
                    regenerated_count += 1
                    results_list.append({
                        "cluster_result_id": cluster_result_id,
                        "status": "success",
                        "plot_path": plot_path
                    })
                else:
                    failed_count += 1
                    results_list.append({
                        "cluster_result_id": cluster_result_id,
                        "status": "failed",
                        "error": "Failed to generate plot"
                    })
            except Exception as e:
                failed_count += 1
                error_msg = str(e)
                print(f"⚠️ 重新生成 {cluster_result_id} 的缩略图失败: {error_msg}")
                results_list.append({
                    "cluster_result_id": cluster_result_id,
                    "status": "failed",
                    "error": error_msg
                })
        
        return {
            "success": True,
            "slice_id": slice_id,
            "total": len(results),
            "regenerated": regenerated_count,
            "failed": failed_count,
            "results": results_list
        }
        
    except Exception as e:
        error_msg = f"重新生成缩略图失败: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/compute-clustering-metrics")
def compute_clustering_metrics_endpoint(
    slice_id: str = Query(..., description="Slice ID"),
    cluster_result_id: str = Query(..., description="Cluster result ID to compute metrics for")
):
    """
    计算指定聚类结果的评估指标（CHAO、轮廓系数、PAS、Moran's I）
    并将结果保存到数据库
    """
    # 保存原始全局 slice_id（使用 globals() 字典访问，避免与参数名冲突）
    original_slice_id = globals().get('slice_id', None)
    request_slice_id = slice_id  # 保存请求参数
    
    try:
        # 设置全局 slice_id 以便 prepare_data 使用
        globals()['slice_id'] = request_slice_id
        
        # 加载数据
        print(f"📊 开始计算聚类评估指标: slice_id={request_slice_id}, cluster_result_id={cluster_result_id}")
        print(f"   📂 正在加载数据...")
        prepare_data(force_reload=False)
        print(f"   ✅ 数据加载完成")
        
        adata = globals().get('adata', None)
        if adata is None:
            raise HTTPException(status_code=404, detail=f"无法加载切片 {request_slice_id} 的数据")
        
        # 复制 adata 以避免修改全局对象
        adata_local = adata.copy()
        
        # 应用指定的聚类结果标签到 adata
        print(f"   📋 正在应用聚类标签...")
        apply_cluster_labels(adata_local, request_slice_id, cluster_result_id, cluster_field="domain")
        print(f"   ✅ 聚类标签应用完成")
        
        # 检查是否有聚类标签
        if "domain" not in adata_local.obs or adata_local.obs["domain"].isna().all():
            raise HTTPException(
                status_code=404, 
                detail=f"聚类结果 {cluster_result_id} 不存在或为空"
            )
        
        # 计算指标
        print(f"   🔢 开始计算评估指标...")
        metrics = compute_clustering_metrics(
            adata_local,
            request_slice_id,
            cluster_result_id,
        )
        print(f"   ✅ 指标计算完成: CHAO={metrics.get('chao')}, Silhouette={metrics.get('silhouette')}, PAS={metrics.get('pas')}, Moran's I={metrics.get('morans_i')}")
        
        # ✅ 计算并存储簇级指标
        try:
            print(f"   🔢 开始计算簇级指标...")
            cluster_metrics = compute_per_cluster_metrics(
                adata_local,
                request_slice_id,
                cluster_result_id,
            )
            if cluster_metrics:
                store_per_cluster_metrics(
                    cluster_metrics,
                    request_slice_id,
                    cluster_result_id,
                )
                print(f"   ✅ 簇级指标计算完成，共 {len(cluster_metrics)} 个簇")
            else:
                print(f"   ⚠️ 没有簇级指标需要存储")
        except Exception as e:
            print(f"⚠️ 计算簇级指标时出错: {e}")
            import traceback
            traceback.print_exc()
        
        # 更新数据库中的指标值
        metadata = MetaData()
        metadata.reflect(bind=engine, only=["cluster_method"])
        cluster_method_table = metadata.tables["cluster_method"]
        
        with engine.begin() as conn:
            # 检查记录是否存在
            exists = conn.execute(
                select(cluster_method_table)
                .where(
                    cluster_method_table.c.slice_id == request_slice_id,
                    cluster_method_table.c.cluster_result_id == cluster_result_id
                )
            ).fetchone()
            
            if exists:
                # 更新指标 - 只更新非 None 的字段，保留其他字段
                update_values = {"updated_at": func.current_timestamp()}
                if metrics.get("chao") is not None:
                    update_values["chao"] = metrics.get("chao")
                if metrics.get("silhouette") is not None:
                    update_values["silhouette"] = metrics.get("silhouette")
                if metrics.get("pas") is not None:
                    update_values["pas"] = metrics.get("pas")
                if metrics.get("morans_i") is not None:
                    update_values["morans_i"] = metrics.get("morans_i")
                
                if len(update_values) > 1:  # 除了 updated_at 还有其他字段
                    conn.execute(
                        update(cluster_method_table)
                        .where(
                            cluster_method_table.c.slice_id == request_slice_id,
                            cluster_method_table.c.cluster_result_id == cluster_result_id
                        )
                        .values(**update_values)
                    )
            else:
                # 如果记录不存在，创建新记录（只包含指标字段）
                insert_values = {
                    "slice_id": request_slice_id,
                    "cluster_result_id": cluster_result_id
                }
                if metrics.get("chao") is not None:
                    insert_values["chao"] = metrics.get("chao")
                if metrics.get("silhouette") is not None:
                    insert_values["silhouette"] = metrics.get("silhouette")
                if metrics.get("pas") is not None:
                    insert_values["pas"] = metrics.get("pas")
                if metrics.get("morans_i") is not None:
                    insert_values["morans_i"] = metrics.get("morans_i")
                
                conn.execute(
                    insert(cluster_method_table).values(**insert_values)
                )
        
        # 恢复全局 slice_id
        if original_slice_id is not None:
            globals()['slice_id'] = original_slice_id
        
        print(f"✅ 聚类评估指标计算完成并已保存到数据库")
        return {
            "status": "success",
            "slice_id": request_slice_id,
            "cluster_result_id": cluster_result_id,
            "metrics": {
                "chao": sanitize_float_for_json(metrics.get("chao")),
                "silhouette": sanitize_float_for_json(metrics.get("silhouette")),
                "pas": sanitize_float_for_json(metrics.get("pas")),
                "morans_i": sanitize_float_for_json(metrics.get("morans_i"))
            },
            "cluster_metrics": cluster_metrics if cluster_metrics else []
        }
        
    except HTTPException:
        # 恢复全局 slice_id
        if original_slice_id is not None:
            globals()['slice_id'] = original_slice_id
        raise
    except Exception as e:
        # 恢复全局 slice_id
        if original_slice_id is not None:
            globals()['slice_id'] = original_slice_id
        print(f"⚠️ 计算聚类评估指标时出错: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"计算聚类评估指标失败: {str(e)}"
        )


@app.get("/cluster-metrics")
def get_cluster_metrics(
    slice_id: str = Query(..., description="Slice ID"),
    cluster_result_id: str = Query(None, description="Cluster result ID. If None, returns metrics for all results")
):
    """
    获取指定聚类结果的簇级指标
    
    参数:
    --------
    slice_id : str
        切片ID
    cluster_result_id : str, optional
        聚类结果ID。如果为None，返回该切片所有结果的簇级指标
    
    返回:
    --------
    dict
        包含簇级指标的字典，格式: {
            "slice_id": "...",
            "cluster_result_id": "...",
            "cluster_metrics": [
                {
                    "cluster": "0",
                    "size": 1234,
                    "silhouette": 0.75,
                    "morans_i": 0.82,
                    "gearys_c": 0.35
                },
                ...
            ]
        }
        如果 cluster_result_id 为 None，返回格式为:
        {
            "slice_id": "...",
            "results": [
                {
                    "cluster_result_id": "...",
                    "cluster_metrics": [...]
                },
                ...
            ]
        }
    """
    try:
        metadata = MetaData()
        try:
            metadata.reflect(bind=engine, only=["cluster_metrics"])
            cluster_metrics_table = metadata.tables["cluster_metrics"]
        except Exception as e:
            print(f"⚠️ 无法加载 cluster_metrics 表: {e}")
            # 检查表是否存在
            with engine.connect() as conn:
                tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='cluster_metrics'")).fetchall()
                if not tables:
                    print("⚠️ cluster_metrics 表不存在，返回空结果")
                    return {
                        "slice_id": slice_id,
                        "cluster_result_id": cluster_result_id if cluster_result_id else None,
                        "cluster_metrics": [],
                        "warning": "cluster_metrics table does not exist"
                    }
                else:
                    raise
        
        with engine.connect() as conn:
            if cluster_result_id is not None:
                # 获取指定结果的簇级指标
                stmt = select(cluster_metrics_table).where(
                    cluster_metrics_table.c.slice_id == slice_id,
                    cluster_metrics_table.c.cluster_result_id == cluster_result_id
                ).order_by(cluster_metrics_table.c.cluster)
                
                print(f"📊 查询簇级指标: slice_id={slice_id}, cluster_result_id={cluster_result_id}")
                results = conn.execute(stmt).fetchall()
                print(f"📊 查询结果数量: {len(results)}")
                
                cluster_metrics = []
                for row in results:
                    cluster_metrics.append({
                        "cluster": row.cluster,
                        "size": row.size,
                        "silhouette": sanitize_float_for_json(row.silhouette),
                        "morans_i": sanitize_float_for_json(row.morans_i),
                        "gearys_c": sanitize_float_for_json(row.gearys_c)
                    })
                
                if len(cluster_metrics) == 0:
                    print(f"⚠️ 未找到簇级指标，可能是数据尚未计算。尝试查询所有相关记录...")
                    # 查询是否有该 slice_id 的记录
                    all_stmt = select(cluster_metrics_table).where(
                        cluster_metrics_table.c.slice_id == slice_id
                    ).limit(10)
                    all_results = conn.execute(all_stmt).fetchall()
                    print(f"📊 该切片的所有簇级指标记录数: {len(all_results)}")
                    if all_results:
                        print(f"📊 示例记录: slice_id={all_results[0].slice_id}, cluster_result_id={all_results[0].cluster_result_id}, cluster={all_results[0].cluster}")
                
                return {
                    "slice_id": slice_id,
                    "cluster_result_id": cluster_result_id,
                    "cluster_metrics": cluster_metrics
                }
            else:
                # 获取该切片所有结果的簇级指标
                stmt = select(cluster_metrics_table).where(
                    cluster_metrics_table.c.slice_id == slice_id
                ).order_by(
                    cluster_metrics_table.c.cluster_result_id,
                    cluster_metrics_table.c.cluster
                )
                
                results = conn.execute(stmt).fetchall()
                
                # 按 cluster_result_id 分组
                results_dict = {}
                for row in results:
                    result_id = row.cluster_result_id
                    if result_id not in results_dict:
                        results_dict[result_id] = []
                    results_dict[result_id].append({
                        "cluster": row.cluster,
                        "size": row.size,
                        "silhouette": sanitize_float_for_json(row.silhouette),
                        "morans_i": sanitize_float_for_json(row.morans_i),
                        "gearys_c": sanitize_float_for_json(row.gearys_c)
                    })
                
                results_list = [
                    {
                        "cluster_result_id": result_id,
                        "cluster_metrics": metrics
                    }
                    for result_id, metrics in results_dict.items()
                ]
                
                return {
                    "slice_id": slice_id,
                    "results": results_list
                }
    except Exception as e:
        print(f"⚠️ 获取簇级指标时出错: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"获取簇级指标失败: {str(e)}")


@app.post("/compute-cluster-metrics")
def compute_cluster_metrics_endpoint(
    slice_id: str = Query(..., description="Slice ID"),
    cluster_result_id: str = Query(..., description="Cluster result ID to compute cluster metrics for")
):
    """
    计算指定聚类结果的簇级指标（轮廓系数、Moran's I、Geary's C）
    并将结果保存到数据库
    """
    try:
        # 保存原始全局 slice_id
        original_slice_id = globals().get('slice_id', None)
        request_slice_id = slice_id
        
        # 加载数据
        print(f"📂 加载切片: {request_slice_id}")
        globals()['slice_id'] = request_slice_id
        globals()['path'] = f"./data/{request_slice_id}"
        prepare_data(force_reload=False)
        
        adata = globals().get('adata', None)
        if adata is None:
            raise HTTPException(status_code=404, detail=f"无法加载切片 {request_slice_id} 的数据")
        
        # 复制 adata 以避免修改全局对象
        adata_local = adata.copy()
        
        # 应用指定的聚类结果标签到 adata
        print(f"   📋 正在应用聚类标签...")
        apply_cluster_labels(adata_local, request_slice_id, cluster_result_id, cluster_field="domain")
        print(f"   ✅ 聚类标签应用完成")
        
        # 检查是否有聚类标签
        if "domain" not in adata_local.obs or adata_local.obs["domain"].isna().all():
            raise HTTPException(
                status_code=404, 
                detail=f"聚类结果 {cluster_result_id} 不存在或为空"
            )
        
        # 计算簇级指标
        print(f"   🔢 开始计算簇级指标...")
        cluster_metrics = compute_per_cluster_metrics(
            adata_local,
            request_slice_id,
            cluster_result_id,
        )
        
        if cluster_metrics:
            store_per_cluster_metrics(
                cluster_metrics,
                request_slice_id,
                cluster_result_id,
            )
            print(f"   ✅ 簇级指标计算完成，共 {len(cluster_metrics)} 个簇")
        else:
            print(f"   ⚠️ 没有簇级指标需要存储")
        
        # 恢复全局 slice_id
        if original_slice_id is not None:
            globals()['slice_id'] = original_slice_id
        
        # Sanitize cluster_metrics before returning
        sanitized_cluster_metrics = []
        for metrics in cluster_metrics:
            sanitized_cluster_metrics.append({
                "cluster": metrics["cluster"],
                "size": metrics["size"],
                "silhouette": sanitize_float_for_json(metrics.get("silhouette")),
                "morans_i": sanitize_float_for_json(metrics.get("morans_i")),
                "gearys_c": sanitize_float_for_json(metrics.get("gearys_c"))
            })
        
        return {
            "status": "success",
            "slice_id": request_slice_id,
            "cluster_result_id": cluster_result_id,
            "cluster_metrics": sanitized_cluster_metrics
        }
        
    except HTTPException:
        # 恢复全局 slice_id
        if original_slice_id is not None:
            globals()['slice_id'] = original_slice_id
        raise
    except Exception as e:
        # 恢复全局 slice_id
        if original_slice_id is not None:
            globals()['slice_id'] = original_slice_id
        print(f"⚠️ 计算簇级指标时出错: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"计算簇级指标失败: {str(e)}")


@app.delete("/cluster-result")
def delete_cluster_result(
    slice_id: str = Query(..., description="Slice ID"),
    cluster_result_id: str = Query(..., description="Cluster result ID to delete")
):
    """
    Delete a cluster result and all associated data.
    
    This will delete:
    - All spot cluster records for this cluster_result_id
    - The cluster_method record
    - All cluster_log records for this cluster_result_id
    - The preview plot image (if exists)
    """
    try:
        metadata = MetaData()
        metadata.reflect(bind=engine)
        
        with engine.begin() as conn:
            # 1. Delete from spot_cluster table
            table_name = f"spot_cluster_{slice_id}"
            try:
                spot_cluster = metadata.tables[table_name]
                deleted_spots = conn.execute(
                    spot_cluster.delete().where(
                        spot_cluster.c.cluster_result_id == cluster_result_id
                    )
                ).rowcount
                print(f"✅ Deleted {deleted_spots} spot records from {table_name}")
            except Exception as e:
                print(f"⚠️ Error deleting from spot_cluster table: {e}")
            
            # 2. Delete from cluster_method table
            try:
                cluster_method = metadata.tables["cluster_method"]
                deleted_method = conn.execute(
                    cluster_method.delete().where(
                        cluster_method.c.slice_id == slice_id,
                        cluster_method.c.cluster_result_id == cluster_result_id
                    )
                ).rowcount
                print(f"✅ Deleted {deleted_method} cluster_method record")
            except Exception as e:
                print(f"⚠️ Error deleting from cluster_method table: {e}")
            
            # 3. Delete from cluster_log table
            try:
                cluster_log = metadata.tables["cluster_log"]
                deleted_logs = conn.execute(
                    cluster_log.delete().where(
                        cluster_log.c.slice_id == slice_id,
                        cluster_log.c.cluster_result_id == cluster_result_id
                    )
                ).rowcount
                print(f"✅ Deleted {deleted_logs} cluster_log records")
            except Exception as e:
                print(f"⚠️ Error deleting from cluster_log table: {e}")
        
        # 4. Delete preview plot image if exists
        try:
            plot_path = Path(f"data/{slice_id}/plots/{slice_id}_{cluster_result_id}_plot.png")
            if plot_path.exists():
                plot_path.unlink()
                print(f"✅ Deleted plot image: {plot_path}")
        except Exception as e:
            print(f"⚠️ Error deleting plot image: {e}")
        
        return {
            "success": True,
            "message": f"Cluster result '{cluster_result_id}' deleted successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting cluster result: {str(e)}")

@app.get("/ncount_by_cluster")
def get_ncount_by_cluster(slice_id: str = Query(...), cluster_result_id: str = "default"):
    table_name = f"spot_cluster_{slice_id}"
    query = text(
        f"""
        SELECT cur.cluster,
               COALESCE(cur.n_count_spatial, base.n_count_spatial, 0) AS n_count_spatial
        FROM `{table_name}` AS cur
        LEFT JOIN `{table_name}` AS base
            ON cur.barcode = base.barcode
           AND base.cluster_result_id = 'default'
        WHERE cur.cluster_result_id = :cluster_result_id
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(query, {"cluster_result_id": cluster_result_id}).fetchall()

    # 聚类分组
    cluster_dict = {}
    for cluster, ncount in rows:
        cluster_name = f"Cluster {cluster}"
        cluster_dict.setdefault(cluster_name, []).append(ncount)

    return cluster_dict


@app.get("/spot-metrics")
def get_spot_metrics(slice_id: str = Query(...), cluster_result_id: str = "default"):
    """
    Spot-level metrics are intrinsic to the slice and do not vary by clustering result.
    We therefore always read them from the baseline ('default') clustering entry while
    keeping the response schema unchanged. The `cluster_result_id` parameter is accepted
    for backward compatibility but ignored in the query.
    """
    table_name = f"spot_cluster_{slice_id}"
    df = pd.DataFrame(
        columns=[
            "barcode",
            "cluster",
            "nCount_Spatial",
            "nFeature_Spatial",
            "percent_mito",
            "percent_ribo",
        ],
    )
    try:
        query = text(f"""
            SELECT base.barcode,
                   base.cluster,
                   base.n_count_spatial   AS nCount_Spatial,
                   base.n_feature_spatial AS nFeature_Spatial,
                   base.percent_mito      AS percent_mito,
                   base.percent_ribo      AS percent_ribo
            FROM `{table_name}` AS base
            WHERE base.cluster_result_id = 'default'
              AND (
                  base.n_count_spatial IS NOT NULL OR
                  base.n_feature_spatial IS NOT NULL OR
                  base.percent_mito IS NOT NULL OR
                  base.percent_ribo IS NOT NULL
              )
        """)
        with engine.connect() as conn:
            rows = conn.execute(query).fetchall()
        if rows:
            df = pd.DataFrame(
                rows,
                columns=[
                    "barcode",
                    "cluster",
                    "nCount_Spatial",
                    "nFeature_Spatial",
                    "percent_mito",
                    "percent_ribo",
                ],
            )
    except Exception as _e:
        print(f"⚠️ spot-metrics 查询表失败 (slice_id={slice_id}): {_e}")

    def _build_fallback_df():
        global adata, loaded_slice_id
        if adata is None or loaded_slice_id != slice_id:
            globals()["slice_id"] = slice_id
            prepare_data(force_reload=False)
        if adata is None:
            return None
        obs = adata.obs.copy()
        idx = obs.index.astype(str)
        pct_mt = obs.get("pct_counts_mt")
        pct_ribo = obs.get("pct_counts_ribo")
        if pct_mt is None:
            pct_mt = pd.Series(0.0, index=obs.index)
        else:
            pct_mt = pct_mt.fillna(0.0)
        if pct_ribo is None:
            pct_ribo = pd.Series(0.0, index=obs.index)
        else:
            pct_ribo = pct_ribo.fillna(0.0)
        ncount = obs.get("nCount_Spatial")
        nfeat = obs.get("nFeature_Spatial")
        if ncount is None:
            ncount = pd.Series(0.0, index=obs.index)
        if nfeat is None:
            nfeat = pd.Series(0.0, index=obs.index)
        return pd.DataFrame(
            {
                "barcode": idx,
                "cluster": obs.get("domain", pd.Series("unknown", index=obs.index)).astype(str),
                "nCount_Spatial": ncount,
                "nFeature_Spatial": nfeat,
                "percent_mito": pct_mt,
                "percent_ribo": pct_ribo,
            }
        )

    need_fallback = (
        df.empty
        or not df["nCount_Spatial"].notna().any()
        or not df["nFeature_Spatial"].notna().any()
        or not df["percent_mito"].notna().any()
        or not df["percent_ribo"].notna().any()
    )
    if need_fallback:
        try:
            fallback_df = _build_fallback_df()
            if fallback_df is not None:
                df = fallback_df
        except Exception as _e:
            print(f"⚠️ spot-metrics fallback 失败 (slice_id={slice_id}): {_e}")
        for col in ["nCount_Spatial", "nFeature_Spatial", "percent_mito", "percent_ribo"]:
            if col not in df.columns or (df[col].isna().all() and len(df) > 0):
                df[col] = 0.0

    # Convert to long format: one row per metric per spot, for faceted violins on frontend
    long_df = df.melt(
        id_vars=["barcode", "cluster"],
        value_vars=["nCount_Spatial", "nFeature_Spatial", "percent_mito", "percent_ribo"],
        var_name="metric",
        value_name="value",
    )
    # 避免 NaN 导致前端小提琴图“无数据”，用 0 填充
    long_df["value"] = long_df["value"].fillna(0.0)

    return long_df.to_dict(orient="records")


class ClusterUpdateRequest(BaseModel):
    slice_id: str
    cluster_result_id: str = "default"
    barcode: str
    old_cluster: str
    new_cluster: str
    comment: str = ""


@app.post("/update-cluster")
def update_cluster(req: ClusterUpdateRequest):
    global adata
    table_name = f"spot_cluster_{req.slice_id}"

    with engine.begin() as conn:
        # 校验 barcode 是否存在且原始 cluster 一致
        result = conn.execute(
            text(f"SELECT cluster FROM `{table_name}` WHERE barcode = :barcode AND cluster_result_id = :cluster_result_id"),
            {"barcode": req.barcode, "cluster_result_id": req.cluster_result_id}
        ).fetchone()

        # clusterNumbers = oldClusterStrings.map(s => parseInt(s.replace(/\D/g, '')));

        if result is None:
            raise HTTPException(status_code=404, detail="Barcode not found")

        if result[0] != req.old_cluster:
            raise HTTPException(status_code=400, detail="Old cluster does not match current value")

        # 更新 cluster
        conn.execute(
            text(f"""
                UPDATE `{table_name}`
                SET cluster = :new_cluster
                WHERE barcode = :barcode AND cluster_result_id = :cluster_result_id
            """),
            {"new_cluster": req.new_cluster, "barcode": req.barcode, "cluster_result_id": req.cluster_result_id}
        )

        # 写入日志表
        conn.execute(
            text(f"""
                INSERT INTO cluster_log (slice_id, cluster_result_id, barcode, old_cluster, new_cluster, comment)
                VALUES (:slice_id, :cluster_result_id, :barcode, :old_cluster, :new_cluster, :comment)
            """),
            req.dict()
        )

    # 更新全局 adata 中对应 barcode 的 cluster
    if adata is not None and req.barcode in adata.obs.index:
        try:
            # 更新 adata.obs['domain'] 字段（这是存储聚类结果的字段）
            # 如果当前cluster_result_id是"default"或者是当前使用的，则更新domain
            # 否则可能需要更新其他字段，但通常domain是主要的聚类字段
            if req.cluster_result_id == "default" or req.slice_id == slice_id:
                adata.obs.loc[req.barcode, 'domain'] = req.new_cluster
                # 确保domain是category类型
                if 'domain' in adata.obs.columns:
                    adata.obs['domain'] = adata.obs['domain'].astype('category')
                print(f"✅ Updated adata.obs['domain'] for barcode {req.barcode}: {req.old_cluster} -> {req.new_cluster}")
            else:
                print(f"⚠️ Skipping adata update: cluster_result_id {req.cluster_result_id} is not the current one")
        except Exception as e:
            print(f"⚠️ Error updating adata for barcode {req.barcode}: {str(e)}")
            import traceback
            traceback.print_exc()
    else:
        if adata is None:
            print(f"⚠️ adata is None, cannot update")
        else:
            print(f"⚠️ Barcode {req.barcode} not found in adata.obs.index")

    # 在重聚类complete更新数据库后，重新生成并更新预览图
    # 只有当cluster实际发生更改时才更新预览图
    if req.old_cluster != req.new_cluster:
        try:
            print(f"🖼️ 开始更新聚类结果预览图: slice_id={req.slice_id}, cluster_result_id={req.cluster_result_id}")
            plot_path = save_cluster_plot(req.slice_id, req.cluster_result_id)
            if plot_path:
                # 更新 cluster_method 表中的 plot_path
                metadata = MetaData()
                metadata.reflect(bind=engine)
                cluster_method = metadata.tables["cluster_method"]
                with engine.begin() as conn:
                    conn.execute(
                        update(cluster_method)
                        .where(
                            cluster_method.c.slice_id == req.slice_id,
                            cluster_method.c.cluster_result_id == req.cluster_result_id
                        )
                        .values(plot_path=plot_path)
                    )
                print(f"✅ 预览图已更新: {plot_path}")
            else:
                print(f"⚠️ 预览图生成失败")
        except Exception as e:
            print(f"⚠️ 更新预览图时出错: {str(e)}")
            import traceback
            traceback.print_exc()
            # 不抛出异常，因为数据库更新已经成功

    return {"message": "Cluster updated and logged successfully."}


@app.get("/cluster-log")
def get_cluster_log(slice_id: str = Query(...), cluster_result_id: str = "default"):
    query = text("""
        SELECT barcode, old_cluster, new_cluster, comment, updated_at
        FROM cluster_log
        WHERE slice_id = :slice_id AND cluster_result_id = :cluster_result_id
        ORDER BY updated_at DESC
    """)

    with engine.connect() as conn:
        rows = conn.execute(query, {"slice_id": slice_id, "cluster_result_id": cluster_result_id}).fetchall()

    return [
        {
            "barcode": r[0],
            "old_cluster": r[1],
            "new_cluster": r[2],
            "comment": r[3],
            "updated_at": parse(r[4]).isoformat() if r[4] else None
        }
        for r in rows
    ]


@app.get("/cluster-log-by-spot")
def get_cluster_log_by_spot(
    cluster_result_id: str = Query(..., description="Cluster result ID to filter logs - required to query logs for a specific clustering result"),
    barcode: str = Query(..., description="Spot barcode to filter logs - required to query logs for a specific spot")
):
    """
    查询某个spot在指定聚类结果下的修改日志
    
    根据聚类结果ID和spot ID（barcode）精确查询该spot的所有修改历史记录。
    查询条件：cluster_result_id 和 barcode（不需要slice_id）。
    
    Parameters:
    - cluster_result_id: 聚类结果ID（必填）- 用于指定查询哪个聚类结果下的日志
    - barcode: Spot的条形码（必填）- 用于指定查询哪个spot的日志
    
    Returns:
    - 返回该spot在该聚类结果下的所有修改记录，按时间倒序排列
    - 每条记录包含：barcode, old_cluster, new_cluster, comment, updated_at
    
    Note:
    - 查询仅基于 cluster_result_id 和 barcode，确保返回的是该spot在指定聚类结果下的修改历史
    - 如果同一个spot在不同的聚类结果下都有修改记录，需要分别查询
    """
    # 查询条件：仅使用 cluster_result_id 和 barcode
    query = text("""
        SELECT barcode, old_cluster, new_cluster, comment, updated_at
        FROM cluster_log
        WHERE cluster_result_id = :cluster_result_id 
          AND barcode = :barcode
        ORDER BY updated_at DESC
    """)

    with engine.connect() as conn:
        rows = conn.execute(query, {
            "cluster_result_id": cluster_result_id, 
            "barcode": barcode
        }).fetchall()

    # 处理 updated_at 字段：可能是 datetime 对象或字符串
    from datetime import datetime
    result = []
    for r in rows:
        updated_at = r[4]
        if updated_at is None:
            updated_at_str = None
        elif isinstance(updated_at, str):
            # 如果已经是字符串，直接使用
            updated_at_str = updated_at
        elif hasattr(updated_at, 'isoformat'):
            # 如果是 datetime 对象，转换为 ISO 格式字符串
            updated_at_str = updated_at.isoformat()
        else:
            # 其他情况，转换为字符串
            updated_at_str = str(updated_at)
        
        result.append({
            "barcode": r[0],
            "old_cluster": r[1],
            "new_cluster": r[2],
            "comment": r[3],
            "updated_at": updated_at_str
        })
    
    return result

class selectedBarcodes(BaseModel):
    slice_id: str
    barcode: List[str]
    method: str
    
    
@app.post("/recluster")
async def recluster(
    slice_id: Annotated[str, Form()],
    barcode: Annotated[str, Form()],
    method: Annotated[str, Form()],
    cluster_result_id: Annotated[str, Form()] = "default",
    stability_protect_threshold: Annotated[float, Form()] = 0.9,
    image: Optional[UploadFile] = None,
    factor=factor
):
    """
    重聚类分析接口（仅使用改进的 GMM / Admixture 方法）
    """
    global adata
    print(f"开始重聚类分析，slice_id: {slice_id}")
    
    try:
        
        # 导入重聚类方法
        from Admixture_reclustering_v2 import improved_admixture_reclustering
        
        # selected_barcodes = list(barcode)  # 确保是列表类型
        raw_barcodes = json.loads(barcode)
        print("raw_barcodes:", raw_barcodes)
        
        # 处理两种格式: 纯字符串数组 或 对象数组 (从 Sankey 跳转来的)
        selected_barcodes = []
        provided_stability = {}  # barcode -> stability in [0,1]
        for item in raw_barcodes:
            if isinstance(item, str):
                selected_barcodes.append(item)
            elif isinstance(item, dict) and 'barcode' in item:
                selected_barcodes.append(item['barcode'])
                # Optional: frontend may attach per-spot stability
                for key in ("stability", "stability_score", "stability_percent", "stabilityPercent"):
                    if key in item and item[key] is not None:
                        try:
                            v = float(item[key])
                            # allow either 0-1 or 0-100
                            if v > 1.0:
                                v = v / 100.0
                            v = max(0.0, min(1.0, v))
                            provided_stability[str(item["barcode"])] = v
                            break
                        except Exception:
                            pass
        
        print("selected_barcodes:", selected_barcodes)
        barcode_count = len(selected_barcodes)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")

        filename = f"{slice_id}_{barcode_count}_{timestamp}_preview.png"

        if image is not None:
            contents = await image.read()
            with open(filename, "wb") as f:
                f.write(contents)
            print(f"图片已保存: {filename}")
        else:
            print("未上传图片，跳过图片处理")
        
        print(f"选中的条形码数量: {len(selected_barcodes)}")
        
        # 1. 创建选中区域的mask
        apply_cluster_labels(adata, slice_id, cluster_result_id, "domain")
        if "emb" not in adata.obsm:
            print("⚠️ recluster 时未找到 emb，尝试从数据库加载失败或为空")

        selected_mask = adata.obs.index.isin(selected_barcodes)
        
        if selected_mask.sum() == 0:
            raise ValueError("没有找到匹配的条形码")
        
        # 2. 在adata中添加选中区域标记
        adata.obs['selected_region'] = selected_mask

        # 2.5 计算选中点的“稳定度”（跨其他聚类结果的一致性）
        # 定义：在“其他结果”里出现次数最多的 cluster / 有效历史条数（0~1）
        try:
            history_resp = get_spots_cluster_history(
                SpotsClusterHistoryRequest(
                    barcodes=selected_barcodes,
                    slice_id=slice_id,
                    current_cluster_result_id=cluster_result_id,
                )
            )
            history_map = (history_resp or {}).get("history", {}) or {}
        except Exception as e:
            print(f"⚠️ 计算 cluster history 失败，将跳过稳定度: {e}")
            history_map = {}

        barcode_to_stability = {}
        for bc in selected_barcodes:
            hist = history_map.get(bc, []) or []
            # Filter unknown methods to match frontend logic
            valid = [h for h in hist if h.get("method") and h.get("method") != "unknown" and h.get("cluster") is not None]
            total = len(valid)
            if total <= 0:
                continue
            counts = {}
            for h in valid:
                c = str(h.get("cluster"))
                counts[c] = counts.get(c, 0) + 1
            max_count = max(counts.values()) if counts else 0
            barcode_to_stability[bc] = (max_count / total) if total else None

        # Prefer frontend-provided stability (if present)
        barcode_to_stability.update(provided_stability)

        # Attach stability to adata for downstream reclustering / reporting
        try:
            adata.obs["selection_stability"] = np.nan
            for bc, s in barcode_to_stability.items():
                if s is None:
                    continue
                if bc in adata.obs.index:
                    adata.obs.loc[bc, "selection_stability"] = float(s)
        except Exception as e:
            print(f"⚠️ 写入 selection_stability 失败: {e}")
        
        # 3. 运行重聚类分析（仅使用 GMM / Admixture）
        print("运行重聚类分析，使用算法: gmm (改进的 Admixture)")
        
        # 确保基因名唯一性
        if not adata.var_names.is_unique:
            print("⚠️ 全局adata基因名不唯一，正在修复...")
            adata.var_names_make_unique()
            print("✅ 全局adata基因名已修复")
        
        # 检查可用的特征
        available_features = list(adata.obsm.keys())
        print(f"可用特征: {available_features}")
        print(f"method: {method}")
        print("adata:", adata)

        # ========== 仅保留原有的 GMM (Admixture) 算法 ==========
        print("=" * 50)
        print("使用改进的Admixture重聚类分析 (GMM)")
        print("=" * 50)
        
        if method == "SpaGCN":
            feature_key = "spagcn_embed"
        elif method == "GraphST":
            feature_key = "emb"
        elif method == "SEDR":
            feature_key = "SEDR"
        else:
            feature_key = "X_pca"
            
        # 自动根据现有聚类数量设置n_components_range
        if 'domain' in adata.obs:
            n_clusters = adata.obs['domain'].nunique()
            n_components_range = (1, n_clusters + 1)
        else:
            n_components_range = (2, 5)
        adata_var = adata[:, adata.var['highly_variable']].copy()
        for key, value in adata.obsm.items():
            if isinstance(value, np.ndarray):
                adata_var.obsm[key] = value.copy()
        for key, value in adata.obsp.items():
            adata_var.obsp[key] = value
        # 若存在 data/{slice_id}/{slice_id}_truth.txt 则传入，用于在 console 打印优化前/后准确率
        truth_path = os.path.join("data", slice_id, f"{slice_id}_truth.txt")
        if not os.path.isfile(truth_path):
            truth_path = None
        adata_updated = improved_admixture_reclustering(
            adata_var,
            region_key = 'selected_region',
            n_components_range = n_components_range,  # 自动设置
            use_spatial = True,
            spatial_weight = 0.3,
            feature_key = feature_key,  # 使用GraphST的embedding
            confidence_threshold=0.7,
            min_cluster_size = 5,  # 适中的最小聚类大小
            use_hvg = True,
            n_hvg = 200,
            n_components_pca = 50,
            mapping_strategy = 'feature_functional',
            selection_stability_key="selection_stability",
            stability_protect_threshold=stability_protect_threshold,
            truth_path=truth_path,
            slice_id=slice_id,
        )
        
        # 4. 将结果转换为与原接口兼容的格式
        print("转换结果格式...")
        cluster_change_df = _convert_admixture_results_to_original_format(
            adata_updated, selected_barcodes, slice_id
        )
        
        # 5. 不更新全局adata - 只有用户接受后才更新
        # adata = adata_updated  # 注释掉，不立即更新
        
        # 6. 不保存结果到uns中 - 只有用户接受后才更新
        # adata.uns["change_df"] = cluster_change_df  # 注释掉
        
        # 7. 不保存处理后的数据 - 只有用户接受后才更新
        # try:
        #     output_path = f"{str(slice_id)}_processed_admixture.h5ad"
        #     adata.write_h5ad(output_path)
        #     print(f"成功保存到 {output_path}")
        # except Exception as e:
        #     print(f"保存h5ad文件时出错: {str(e)}")
        
        # 8. 返回清理后的结果 - 参考原始格式
        # 确保DataFrame不使用barcode作为索引，而是作为列
        if cluster_change_df.index.name == 'barcode' or 'barcode' not in cluster_change_df.columns:
            cluster_change_df = cluster_change_df.reset_index()
            if 'index' in cluster_change_df.columns and 'barcode' not in cluster_change_df.columns:
                cluster_change_df = cluster_change_df.rename(columns={'index': 'barcode'})
        
        # 清理数据，将NaN和无穷值替换为None
        obs_cleaned = cluster_change_df.replace({np.nan: None, np.inf: None, -np.inf: None})
        
        # 确保关键字段存在且格式正确
        required_fields = ['barcode', 'original_cluster', 'new_cluster', 'changed']
        optional_fields = ['confidence', 'p_value', 'mapping_source', 'relationship', 'max_prob', 'hard_label']
        
        for field in required_fields:
            if field not in obs_cleaned.columns:
                print(f"⚠️ 缺少关键字段: {field}")
                if field == 'changed':
                    obs_cleaned['changed'] = False  # 默认值
        
        for field in optional_fields:
            if field not in obs_cleaned.columns:
                if field == 'confidence':
                    obs_cleaned['confidence'] = 0.5  # 默认置信度
                elif field == 'p_value':
                    obs_cleaned['p_value'] = 0.5  # 默认p值
                elif field in ['mapping_source', 'relationship']:
                    obs_cleaned[field] = 'unknown'
                elif field == 'max_prob':
                    obs_cleaned[field] = 0.5
        
        # 确保数据类型正确
        if 'confidence' in obs_cleaned.columns:
            obs_cleaned['confidence'] = pd.to_numeric(obs_cleaned['confidence'], errors='coerce').fillna(0.5)
        if 'p_value' in obs_cleaned.columns:
            obs_cleaned['p_value'] = pd.to_numeric(obs_cleaned['p_value'], errors='coerce').fillna(0.5)
        if 'changed' in obs_cleaned.columns:
            obs_cleaned['changed'] = obs_cleaned['changed'].astype(bool)
        if 'new_cluster' in obs_cleaned.columns:
            obs_cleaned = obs_cleaned[pd.to_numeric(obs_cleaned['new_cluster'], errors='coerce').notnull()]
            obs_cleaned['new_cluster'] = obs_cleaned['new_cluster']
        
        # 最终检查
        print(f"\n最终返回数据检查:")
        print(f"  数据形状: {obs_cleaned.shape}")
        print(f"  列名: {list(obs_cleaned.columns)}")
        print(f"  barcode示例: {obs_cleaned['barcode'].head(3).tolist() if 'barcode' in obs_cleaned.columns else 'N/A'}")
        print(f"  changed字段统计: {obs_cleaned['changed'].value_counts().to_dict() if 'changed' in obs_cleaned.columns else 'N/A'}")
        
        # 9. 不更新数据库 - 只有用户接受后才通过update-cluster接口更新
        # 重聚类结果只返回给前端，不进行持久化
        print(f"\n📝 重聚类完成，返回结果给前端（不更新数据库）")
        print(f"   共处理 {len(obs_cleaned)} 条记录")
        changed_count = obs_cleaned.get('changed', pd.Series([False] * len(obs_cleaned))).sum()
        print(f"   其中 {changed_count} 条发生变化")
            # 不抛出异常，继续返回结果
        
        return obs_cleaned.to_dict(orient="records")
        
    except Exception as e:
        print(f"重聚类处理过程中出错: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"重聚类分析失败: {str(e)}")


def _convert_admixture_results_to_original_format(adata_updated, selected_barcodes, slice_id):
    """
    将Admixture重聚类结果转换为与原接口兼容的格式
    参考原始随机森林实现的格式
    """
    print("转换Admixture重聚类结果格式...")
    
    # 筛选出选中的barcodes - 这些是result_index
    selected_mask = adata_updated.obs.index.isin(selected_barcodes)
    selected_data = adata_updated.obs[selected_mask].copy()
    result_index = selected_data.index  # 相当于原来的predict_data.index
    
    print(f"处理 {len(result_index)} 个选中的barcodes")
    
    # 检查可用列
    available_columns = selected_data.columns.tolist()
    print(f"可用列: {available_columns}")
    
    # 创建结果DataFrame - 参考原始格式
    cluster_change_df = pd.DataFrame(index=result_index)
    
    # 1. barcode - 使用索引
    cluster_change_df['barcode'] = result_index
    
    # 2. original_cluster - 原始聚类标签
    cluster_change_df['original_cluster'] = selected_data['domain'].astype(str)
    cluster_change_df['new_cluster'] = selected_data['recluster_result'].astype(str)
        # print("使用映射结果作为new_cluster")
    # 3. new_cluster - 新聚类标签
    # 优先使用映射结果，如果没有则使用硬标签
    if 'recluster_result' in selected_data.columns:
        cluster_change_df['new_cluster'] = selected_data['recluster_result'].astype(str)
        print("使用映射结果作为new_cluster")
    else:
        cluster_change_df['new_cluster'] = selected_data['recluster_hard_labels'].astype(str)
        print("使用硬标签作为new_cluster")
    
    # 4. confidence - 置信度（映射置信度）
    if 'recluster_mapping_confidence' in selected_data.columns:
        cluster_change_df['confidence'] = selected_data['recluster_mapping_confidence'].fillna(0.5)
    elif 'recluster_max_prob' in selected_data.columns:
        # 使用最大概率作为置信度
        cluster_change_df['confidence'] = selected_data['recluster_max_prob'].fillna(0.5)
    else:
        cluster_change_df['confidence'] = 0.5  # 默认置信度
    
    # 5. 概率分布 - 查找并添加概率列
    prob_columns = [col for col in selected_data.columns if col.startswith('recluster_component_') and col.endswith('_prob')]
    
    if prob_columns:
        print(f"找到 {len(prob_columns)} 个概率分布列: {prob_columns}")
        
        # 方法：建立 component ID 到映射后聚类标签的映射
        # 对于每个 component，找到映射到哪个聚类标签
        component_to_cluster = {}
        
        if 'recluster_hard_labels' in selected_data.columns and 'recluster_result' in selected_data.columns:
            # 遍历数据找到每个 component 对应的映射结果
            for idx in selected_data.index:
                hard_label = str(int(selected_data.loc[idx, 'recluster_hard_labels']))
                mapped_cluster = str(selected_data.loc[idx, 'recluster_result'])
                
                if hard_label not in component_to_cluster:
                    component_to_cluster[hard_label] = mapped_cluster
            
            print(f"Component 到聚类的映射: {component_to_cluster}")
            
            # 根据映射关系重命名概率列
            for prob_col in prob_columns:
                # 从 recluster_component_0_prob 中提取 component ID
                parts = prob_col.split('_')
                if len(parts) >= 3:
                    component_id = parts[2]  # '0', '1', '2', ...
                    
                    # 查找这个 component 映射到哪个聚类
                    if component_id in component_to_cluster:
                        mapped_cluster = component_to_cluster[component_id]
                        new_col_name = f"prob_{mapped_cluster}"
                        cluster_change_df[new_col_name] = selected_data[prob_col].fillna(0.0)
                        print(f"  映射概率列: {prob_col} (component {component_id}) -> {new_col_name} (cluster {mapped_cluster})")
                    else:
                        # 如果找不到映射，使用 component ID
                        new_col_name = f"prob_{component_id}"
                        cluster_change_df[new_col_name] = selected_data[prob_col].fillna(0.0)
                        print(f"  ⚠️ Component {component_id} 没有映射，使用默认名称: {new_col_name}")
        else:
            # 如果没有硬标签或映射结果，直接使用 component ID
            print("⚠️ 缺少 recluster_hard_labels 或 recluster_result，直接使用 component ID")
            for prob_col in prob_columns:
                parts = prob_col.split('_')
                if len(parts) >= 3:
                    component_id = parts[2]
                    new_col_name = f"prob_{component_id}"
                    cluster_change_df[new_col_name] = selected_data[prob_col].fillna(0.0)
                    print(f"  复制概率列: {prob_col} -> {new_col_name}")
    else:
        print("⚠️ 未找到概率分布列")
    
    # 6. 映射关系和来源
    if 'recluster_mapping_source' in selected_data.columns:
        cluster_change_df['mapping_source'] = selected_data['recluster_mapping_source'].fillna('unknown')
    
    if 'recluster_relationship' in selected_data.columns:
        cluster_change_df['relationship'] = selected_data['recluster_relationship'].fillna('unknown')
    
    # 7. 聚类稳定性指标
    if 'recluster_max_prob' in selected_data.columns:
        cluster_change_df['max_prob'] = selected_data['recluster_max_prob'].fillna(0.0)
    
    if 'recluster_hard_labels' in selected_data.columns:
        cluster_change_df['hard_label'] = selected_data['recluster_hard_labels'].astype(str)

    # 7.5 选中点的历史稳定度（0~1）
    if 'selection_stability' in selected_data.columns:
        cluster_change_df['selection_stability'] = pd.to_numeric(
            selected_data['selection_stability'], errors='coerce'
        ).fillna(0.0)

    if 'recluster_protected_by_stability' in selected_data.columns:
        cluster_change_df['protected_by_stability'] = selected_data['recluster_protected_by_stability'].fillna(False).astype(bool)
    
    # 7.6 多结果 Refine 的 UI 字段（alt_gap, top3, stability, suggestion_strength）
    if 'recluster_alt_gap' in selected_data.columns:
        cluster_change_df['alt_gap'] = selected_data['recluster_alt_gap']
    if 'recluster_top3' in selected_data.columns:
        cluster_change_df['top3'] = selected_data['recluster_top3']
    if 'recluster_stability' in selected_data.columns:
        cluster_change_df['stability'] = selected_data['recluster_stability']
    if 'recluster_suggestion_strength' in selected_data.columns:
        cluster_change_df['suggestion_strength'] = selected_data['recluster_suggestion_strength']
    
    # 8. changed - 是否改变（关键字段）
    cluster_change_df['changed'] = (cluster_change_df['original_cluster'] != cluster_change_df['new_cluster'])
    
    # 9. p_value - 基于置信度计算的p值
    if 'confidence' in cluster_change_df.columns:
        cluster_change_df['p_value'] = 1 - cluster_change_df['confidence']
    else:
        cluster_change_df['p_value'] = 0.5
    
    # 显示统计信息
    print(f"原始标签分布: {cluster_change_df['original_cluster'].value_counts().to_dict()}")
    print(f"新标签分布: {cluster_change_df['new_cluster'].value_counts().to_dict()}")
    print(f"变化统计: {cluster_change_df['changed'].sum()} / {len(cluster_change_df)} 个spots发生变化")
    
    # 显示聚类变化详情
    if cluster_change_df['changed'].sum() > 0:
        changed_samples = cluster_change_df[cluster_change_df['changed']]
        print("聚类变化详情:")
        change_summary = changed_samples.groupby(['original_cluster', 'new_cluster']).size().reset_index(name='count')
        for _, row in change_summary.iterrows():
            print(f"  {row['original_cluster']} -> {row['new_cluster']}: {row['count']} 个样本")
    
    # 验证关键字段不为null
    null_checks = {
        'barcode': cluster_change_df['barcode'].isnull().sum(),
        'original_cluster': cluster_change_df['original_cluster'].isnull().sum(),
        'new_cluster': cluster_change_df['new_cluster'].isnull().sum(),
        # 'confidence': cluster_change_df['confidence'].isnull().sum(),
        'changed': cluster_change_df['changed'].isnull().sum(),
    }
    
    print("空值检查:")
    for field, null_count in null_checks.items():
        if null_count > 0:
            print(f"  ⚠️  {field}: {null_count} 个空值")
        else:
            print(f"  ✅ {field}: 无空值")
    
    # 显示前几行数据作为示例
    print("\n前5行数据示例:")
    display_cols = ['barcode', 'original_cluster', 'new_cluster', 'confidence', 'changed', 'p_value']
    available_display_cols = [col for col in display_cols if col in cluster_change_df.columns]
    print(cluster_change_df[available_display_cols].head())
    
    print(f"✅ 转换完成，结果包含 {len(cluster_change_df)} 个样本")
    
    return cluster_change_df
        
        
def extract_features(adata, barcodes=None, use_hvg_only=True, n_pcs=20):
    """
    从adata中提取特征，包括:
    1. 标准化的空间坐标 (spatial)
    2. 高变基因表达主成分 (如果指定)
    3. 或所有基因的PCA
    
    所有特征都会被适当标准化
    """
    # 如果没有指定barcodes，则使用所有条码
    if barcodes is None:
        barcodes = adata.obs.index.tolist()
        
    # 确保所有条码都在adata中
    valid_barcodes = [b for b in barcodes if b in adata.obs.index]
    if len(valid_barcodes) == 0:
        raise ValueError("没有有效的条码!")
    
    feature_dfs = []
    
    # 1. 提取并标准化空间坐标
    spatial_coords = pd.DataFrame(adata.obsm['spatial'][adata.obs.index.isin(valid_barcodes)], 
                                 index=[b for b in valid_barcodes])
    
    # 标准化空间坐标
    scaler_spatial = StandardScaler()
    spatial_scaled = scaler_spatial.fit_transform(spatial_coords)
    spatial_df = pd.DataFrame(
        spatial_scaled,
        columns=['x_scaled', 'y_scaled'],
        index=valid_barcodes
    )
    feature_dfs.append(spatial_df)
    print(f"提取了空间坐标特征 (标准化)")
    
    # 2. 提取基因表达数据
    # 使用已经计算好的PCA结果
    if 'X_pca' in adata.obsm:
        pca_df = pd.DataFrame(
            adata.obsm['X_pca'][adata.obs.index.isin(valid_barcodes)],
            columns=[f'PC{i+1}' for i in range(adata.obsm['X_pca'].shape[1])],
            index=valid_barcodes
        )
        feature_dfs.append(pca_df)
        print(f"提取了 {pca_df.shape[1]} 个PCA主成分")
    
    # 合并所有特征
    if not feature_dfs:
        raise ValueError("没有提取到任何有效特征!")
        
    features = pd.concat(feature_dfs, axis=1)
    print(f"最终特征矩阵形状: {features.shape}")
    return features

@app.get("/umap-coordinates")
def get_umap_coordinates(slice_id: str = Query(...), cluster_result_id: str = "default"):
    """
    获取 UMAP 坐标，优先从数据库读取，如果不存在则计算并保存
    """
    global adata
    
    # 保存原始全局 slice_id（如果需要恢复）
    original_slice_id = globals().get('slice_id', None)
    request_slice_id = slice_id  # 保存请求参数
    table_name = f"spot_cluster_{request_slice_id}"  # 在函数作用域内定义
    
    try:
        # 设置全局 slice_id 以便 prepare_data 使用正确的切片
        globals()['slice_id'] = request_slice_id
        
        # 1. 先尝试从数据库读取 UMAP 坐标
        try:
            with engine.connect() as conn:
                # 检查是否有针对当前 cluster_result_id 的 umap 列
                result_cols = conn.execute(text(f"PRAGMA table_info(`{table_name}`)")).fetchall()
                existing_cols = [col[1] for col in result_cols]
                
                umap_x_col = f"umap_1_{cluster_result_id}"
                umap_y_col = f"umap_2_{cluster_result_id}"
                if umap_x_col in existing_cols and umap_y_col in existing_cols:
                    # 从数据库读取
                    query = text(
                        f"""
                        SELECT barcode, `{umap_x_col}` AS umap_1, `{umap_y_col}` AS umap_2, cluster 
                        FROM `{table_name}` 
                        WHERE cluster_result_id = :cluster_result_id
                        AND `{umap_x_col}` IS NOT NULL AND `{umap_y_col}` IS NOT NULL
                        """
                    )
                    rows = conn.execute(query, {"cluster_result_id": cluster_result_id}).fetchall()

                    if rows:
                        df = pd.DataFrame(rows, columns=["barcode", "UMAP_1", "UMAP_2", "cluster"])
                        df.replace([np.inf, -np.inf], np.nan, inplace=True)
                        df.dropna(subset=["UMAP_1", "UMAP_2"], inplace=True)
                        print(f"✅ 从数据库读取 UMAP 坐标 (slice_id={request_slice_id}, cluster_result_id={cluster_result_id})")
                        return df.reset_index(drop=True).to_dict(orient="records")
        except Exception as e:
            print(f"⚠️ 从数据库读取 UMAP 失败: {e}，将重新计算")
        
        # 2. 如果数据库中没有，则计算 UMAP
        print(f"🔄 开始计算 UMAP (slice_id={request_slice_id}, cluster_result_id={cluster_result_id})")
        prepare_data()
        if adata is None:
            raise HTTPException(status_code=500, detail="❌ 数据加载失败，adata 为空")
        
        adata_local = adata.copy()
        
        # 如果 cluster_result_id 不是 "default"，需要加载对应的 embedding 和聚类标签
        if cluster_result_id != "default":
            print(f"📥 加载 cluster_result_id={cluster_result_id} 的 embedding 和聚类标签...")
            apply_cluster_labels(adata_local, request_slice_id, cluster_result_id, cluster_field="domain")
            # 检查是否成功加载了 embedding
            if "emb" not in adata_local.obsm or adata_local.obsm["emb"].shape[0] == 0:
                error_msg = f"❌ 无法加载 cluster_result_id={cluster_result_id} 的 embedding。请确认该聚类结果已存在且包含 embedding 数据。"
                print(error_msg)
                raise HTTPException(status_code=404, detail=error_msg)
            else:
                print(f"✅ 成功加载 embedding，维度: {adata_local.obsm['emb'].shape}")
        else:
            # 对于 "default"，检查 prepare_data() 是否成功加载了 embedding
            if "emb" not in adata_local.obsm or adata_local.obsm["emb"].shape[0] == 0:
                print("⚠️ default cluster_result_id 的 embedding 为空，尝试从数据库加载...")
                # 尝试从数据库加载 default 的 embedding
                apply_cluster_labels(adata_local, request_slice_id, "default", cluster_field="domain")
                if "emb" not in adata_local.obsm or adata_local.obsm["emb"].shape[0] == 0:
                    error_msg = "❌ 无法加载 default 的 embedding。请先运行聚类算法生成 embedding。"
                    print(error_msg)
                    raise HTTPException(status_code=404, detail=error_msg)
                else:
                    print(f"✅ 成功从数据库加载 default embedding，维度: {adata_local.obsm['emb'].shape}")

        if "X_umap" not in adata_local.obsm:
            print("🔄 计算 UMAP 坐标...")
            
            # 检查数据是否有效
            if adata_local.X.shape[0] == 0:
                raise HTTPException(status_code=500, detail="❌ 数据为空，无法计算 UMAP")
            
            # Check if data appears to be already log-transformed to avoid warning
            try:
                if not (adata_local.X.min() >= 0 and adata_local.X.max() <= 20):
                    sc.pp.normalize_total(adata_local)
                    sc.pp.log1p(adata_local)
            except Exception as e:
                print(f"⚠️ 数据预处理警告: {e}，继续执行")
            
            # 如果 emb 已存在则直接用
            if "emb" not in adata_local.obsm:
                error_msg = f"❌ 缺少 embedding 信息（obsm['emb']），无法计算 UMAP。请先运行聚类算法生成 embedding（cluster_result_id={cluster_result_id}）。"
                print(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)
            
            # 检查 embedding 是否有效
            if adata_local.obsm["emb"].shape[0] == 0:
                error_msg = "❌ embedding 矩阵为空，无法计算 UMAP"
                print(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)

            # 在子进程中计算 UMAP，避免 Numba TBB 在非主线程 fork 卡死
            try:
                X_umap = _compute_umap_in_subprocess(
                    adata_local.obsm["emb"],
                    adata_local.obs_names.tolist(),
                )
                adata_local.obsm["X_umap"] = X_umap
            except Exception as e:
                error_msg = f"❌ UMAP 计算失败: {str(e)}"
                print(error_msg)
                import traceback
                print(traceback.format_exc())
                raise HTTPException(status_code=500, detail=error_msg)

            # 更新全局 adata
            adata.obsm["X_umap"] = adata_local.obsm["X_umap"]
    except HTTPException:
        # 重新抛出 HTTPException
        raise
    except Exception as e:
        error_msg = f"❌ UMAP 计算过程中发生错误: {str(e)}"
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)
    
    # 3. 保存 UMAP 到数据库
    try:
        metadata = MetaData()
        metadata.reflect(bind=engine)
        spot_cluster = metadata.tables[table_name]
        
        # 检查是否需要添加 umap_1 和 umap_2 列
        with engine.connect() as conn:
            result_cols = conn.execute(text(f"PRAGMA table_info(`{table_name}`)")).fetchall()
            existing_cols = [col[1] for col in result_cols]
            umap_x_col = f"umap_1_{cluster_result_id}"
            umap_y_col = f"umap_2_{cluster_result_id}"
            if umap_x_col not in existing_cols:
                conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `{umap_x_col}` FLOAT"))
            if umap_y_col not in existing_cols:
                conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `{umap_y_col}` FLOAT"))
            conn.commit()
        
        # 批量更新 UMAP 坐标（使用批量更新提高效率）
        umap_coords = adata_local.obsm["X_umap"]
        update_data = []
        for i, barcode in enumerate(adata_local.obs_names):
            if i < len(umap_coords):
                update_data.append({
                    "barcode": barcode,
                    "cluster_result_id": cluster_result_id,
                    "umap_1": float(umap_coords[i, 0]),
                    "umap_2": float(umap_coords[i, 1])
                })
        
        # 使用批量更新
        with engine.begin() as conn:
            batch_size = 100
            for i in range(0, len(update_data), batch_size):
                batch = update_data[i:i + batch_size]
                for data in batch:
                    conn.execute(
                        update(spot_cluster)
                        .where(
                            spot_cluster.c.barcode == data["barcode"],
                            spot_cluster.c.cluster_result_id == data["cluster_result_id"]
                        )
                        .values(**{umap_x_col: data["umap_1"], umap_y_col: data["umap_2"]})
                    )
        print("✅ UMAP 坐标已保存到数据库")
    except Exception as e:
        print(f"⚠️ 保存 UMAP 到数据库失败: {e}，继续返回结果")
    
    # 4. 返回结果
    try:
        if "X_umap" not in adata_local.obsm:
            raise HTTPException(status_code=500, detail="❌ UMAP 坐标未计算完成")
        
        df = pd.DataFrame(
            adata_local.obsm["X_umap"],
            index=adata_local.obs_names,  
            columns=["UMAP_1", "UMAP_2"]
        )

        df["barcode"] = df.index
        
        # 安全地获取 cluster 信息
        if "domain" in adata_local.obs.columns:
            df["cluster"] = adata_local.obs.loc[df.index, "domain"].astype(str)
        elif "cluster" in adata_local.obs.columns:
            df["cluster"] = adata_local.obs.loc[df.index, "cluster"].astype(str)
        else:
            print("⚠️ 未找到 cluster 或 domain 列，使用默认值 'unknown'")
            df["cluster"] = "unknown"

        df.replace([np.inf, -np.inf], np.nan, inplace=True)
        df.dropna(subset=["UMAP_1", "UMAP_2"], inplace=True)
        
        if df.empty:
            raise HTTPException(status_code=500, detail="❌ UMAP 坐标计算结果为空")

        return df.reset_index(drop=True).to_dict(orient="records")
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"❌ 构建返回结果时发生错误: {str(e)}"
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)
    finally:
        # 恢复原始的 slice_id（如果存在）
        if original_slice_id is not None:
            globals()['slice_id'] = original_slice_id

    
@app.get("/hvg-enrichment")   
def hvg_enrichment(
    slice_id: str = Query(None),
    cluster_result_id: str = "default",
    cluster_field: str = "domain",
    cluster_overrides: str = Query(None, description="Optional JSON dict of barcode->new_cluster for demo overrides"),
):
    """
    对adata.obs['domain']中的每个聚类执行功能富集分析
    
    返回:
    dict: 包含每个聚类的富集分析结果
    """
    global adata
    target_slice = slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")

    adata_local = adata.copy()
    overrides = json.loads(cluster_overrides) if cluster_overrides else None
    apply_cluster_labels(adata_local, target_slice, cluster_result_id, cluster_field, overrides)
    adata_local.obs[cluster_field] = pd.Categorical(adata_local.obs[cluster_field])

    all_clusters_results, all_clusters_results_with_interpret = perform_hvg_enrichment(adata_local)
    return all_clusters_results, all_clusters_results_with_interpret


@app.get("/hvg-enrichment-cluster") 
def hvg_enrichment_by_clusters(
    cluster: str = Query(...),
    slice_id: str = Query(None),
    cluster_result_id: str = "default",
    cluster_field: str = "domain",
    cluster_overrides: str = Query(None, description="Optional JSON dict of barcode->new_cluster for demo overrides"),
):
    """
    对adata.obs['domain']中的指定聚类执行功能富集分析
    
    返回:
    dict: 包含指定聚类的富集分析结果
    """
    global adata
    target_slice = slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")

    adata_local = adata.copy()
    overrides = json.loads(cluster_overrides) if cluster_overrides else None
    apply_cluster_labels(adata_local, target_slice, cluster_result_id, cluster_field, overrides)
    adata_local.obs[cluster_field] = pd.Categorical(adata_local.obs[cluster_field])

    if cluster not in adata_local.obs[cluster_field].cat.categories:
        raise HTTPException(status_code=404, detail=f"Cluster '{cluster}' not found for cluster_result_id '{cluster_result_id}'")

    return perform_hvg_enrichment_by_cluster(adata_local, cluster)

@app.get("/cellchat")
def cell_chat(
    slice_id: str = Query(None),
    cluster_result_id: str = "default",
    cluster_field: str = "domain",
    cluster_overrides: str = Query(None, description="Optional JSON dict of barcode->new_cluster for demo overrides"),
):
    """
    分析细胞间通讯，基于空间邻接和配体-受体相互作用
    
    参数:
    slice_id: 切片ID
    cluster_result_id: 聚类结果ID
    cluster_field: 聚类字段名
    
    返回:
    dict: 包含相互作用矩阵和分数的字典
    """
    global adata
    target_slice = slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")

    adata_local = adata.copy()
    overrides = json.loads(cluster_overrides) if cluster_overrides else None
    apply_cluster_labels(adata_local, target_slice, cluster_result_id, cluster_field, overrides)
    adata_local.obs[cluster_field] = pd.Categorical(adata_local.obs[cluster_field])

    return perform_cellchat_analysis(adata_local)

@app.get("/deconvolution")
def deconvolution_analysis(
    slice_id: str = Query(None),
    cluster_result_id: str = "default",
    cluster_field: str = "cluster"
):
    """
    Perform cell type deconvolution analysis for each spot using cell2location.

    This endpoint integrates Visium data (per-slice) with a matched single-cell
    reference dataset stored as `./data/{slice_id}/scRNA.h5ad` and returns the
    **per-spot cell type proportions** for frontend visualization.

    Returns a JSON dict:
    {
        "cell_types": ["CellType1", "CellType2", ...],
        "spots": ["barcode1", "barcode2", ...],
        "proportions": [[p_ct1, p_ct2, ...] for each spot]  # rows sum to 1
    }
    """
    import os

    target_slice = slice_id or globals().get("slice_id")
    if not target_slice:
        raise HTTPException(status_code=400, detail="slice_id is required")

    # --------- Load spatial data (raw counts) ----------
    # Use the same data layout as `prepare_data`, but avoid log-transforming
    # so that cell2location can operate on raw counts.
    data_dir = f"./data/{target_slice}"
    if not os.path.isdir(data_dir):
        raise HTTPException(
            status_code=404,
            detail=f"Data directory not found for slice_id='{target_slice}': {data_dir}",
        )

    try:
        adata_sp = sq.read.visium(path=data_dir)
    except Exception as e:
        print(f"❌ Failed to read Visium data for deconvolution: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read Visium data for slice_id='{target_slice}': {e!s}",
        )

    if not adata_sp.var_names.is_unique:
        adata_sp.var_names_make_unique()

    # Store raw counts explicitly for cell2location
    if "counts" not in adata_sp.layers:
        adata_sp.layers["counts"] = adata_sp.X.copy()

    # --------- Load single-cell reference ----------
    sc_ref_path = os.path.join(data_dir, "scRNA.h5ad")
    if not os.path.exists(sc_ref_path):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Single-cell reference file 'scRNA.h5ad' not found for slice_id="
                f"'{target_slice}'. Please place the reference at: {sc_ref_path}"
            ),
        )

    try:
        adata_sc = sc.read_h5ad(sc_ref_path)
    except Exception as e:
        print(f"❌ Failed to read scRNA reference: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read single-cell reference at '{sc_ref_path}': {e!s}",
        )

    if not adata_sc.var_names.is_unique:
        adata_sc.var_names_make_unique()

    if "cell_type" not in adata_sc.obs:
        raise HTTPException(
            status_code=500,
            detail="scRNA reference must contain obs['cell_type'] as cell type annotation.",
        )

    # Ensure raw counts are stored for reference as well
    if "counts" not in adata_sc.layers:
        adata_sc.layers["counts"] = adata_sc.X.copy()

    # --------- Run cell2location pipeline ----------
    try:
        from cell2location.models import RegressionModel, Cell2location
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail=(
                "cell2location is not installed in the backend environment. "
                "Please install it (e.g. `pip install cell2location`) and restart the server."
            ),
        )

    # 1) Prepare scRNA reference: normalisation + HVG selection
    try:
        sc.pp.normalize_total(adata_sc, target_sum=1e4)
        sc.pp.log1p(adata_sc)
        sc.pp.highly_variable_genes(
            adata_sc, flavor="seurat", n_top_genes=3000, subset=True
        )
    except Exception as e:
        print(f"❌ Error preprocessing scRNA reference for cell2location: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to preprocess scRNA reference for cell2location: {e!s}",
        )

    # Build regression model to obtain average expression signatures per cell type
    try:
        # Configure AnnData for scvi-tools / cell2location
        RegressionModel.setup_anndata(
            adata=adata_sc,
            layer="counts",
            labels_key="cell_type",
        )

        reg_model = RegressionModel(adata_sc)
        # Train with library default device configuration (CPU/GPU auto-handled by scvi-tools).
        reg_model.train(max_epochs=20)

        # Export posterior; cell-type signatures are stored in varm
        reg_model.export_posterior(
            adata_sc,
            sample_kwargs={
                "num_samples": 100,
                "batch_size": min(adata_sc.n_obs, 2500),
            },
        )

        if "means_per_cluster_mu_fg" not in adata_sc.varm:
            raise RuntimeError(
                "means_per_cluster_mu_fg not found in adata_sc.varm after RegressionModel.export_posterior"
            )

        # genes x cell_types
        ref_signatures = adata_sc.varm["means_per_cluster_mu_fg"]
        # Ensure we have a DataFrame for downstream .loc and .index usage
        if not isinstance(ref_signatures, pd.DataFrame):
            ref_signatures = pd.DataFrame(
                ref_signatures,
                index=adata_sc.var_names,
                columns=sorted(adata_sc.obs["cell_type"].unique()),
            )
    except Exception as e:
        print(f"❌ Error running cell2location RegressionModel: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to estimate cell type signatures with cell2location: {e!s}",
        )

    # 2) Prepare Visium spatial data
    try:
        # Normalisation for model training (raw counts kept in layers['counts'])
        sc.pp.normalize_total(adata_sp, target_sum=1e4)
        sc.pp.log1p(adata_sp)
        sc.pp.highly_variable_genes(
            adata_sp, flavor="seurat", n_top_genes=3000, subset=False
        )
    except Exception as e:
        print(f"❌ Error preprocessing Visium data for cell2location: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to preprocess Visium data for cell2location: {e!s}",
        )

    # Align genes between reference signatures and spatial data
    try:
        common_genes = adata_sp.var_names.intersection(ref_signatures.index)
        if len(common_genes) == 0:
            raise HTTPException(
                status_code=500,
                detail=(
                    "No overlapping genes between spatial data and scRNA reference "
                    "for cell2location deconvolution."
                ),
            )

        ref_signatures = ref_signatures.loc[common_genes].copy()
        adata_sp = adata_sp[:, common_genes].copy()
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error aligning genes for cell2location: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to align genes between spatial and scRNA reference: {e!s}",
        )

    # 3) Run cell2location model to get cell-type abundance per spot
    try:
        # Configure AnnData for Cell2location (spatial data)
        Cell2location.setup_anndata(
            adata=adata_sp,
            layer="counts",
        )

        c2l_model = Cell2location(
            adata_sp,
            cell_state_df=ref_signatures,
            N_cells_per_location=30,
            detection_alpha=20,
        )

        # Train with library default device configuration
        c2l_model.train(max_epochs=50)

        # Export posterior; abundance matrix is stored in obsm
        adata_post = c2l_model.export_posterior(
            adata_sp,
            sample_kwargs={"num_samples": 100, "batch_size": adata_sp.n_obs},
        )
    except Exception as e:
        print(f"❌ Error running cell2location model: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"cell2location model fitting failed during deconvolution: {e!s}",
        )

    # cell2location returns absolute abundance; convert to per-spot proportions
    try:
        print(f"🔍 Available keys in adata_post.obsm: {list(adata_post.obsm.keys())}")

        abundance_key_candidates = [
            "means_cell_abundance_w_sf",  # try mean first (usually more informative)
            "q05_cell_abundance_w_sf",
            "cell_abundance_w_sf",
        ]
        abundance_key = None
        for k in abundance_key_candidates:
            if k in adata_post.obsm:
                abundance_key = k
                print(f"✅ Found abundance key: {k}")
                break

        if abundance_key is None:
            # Try to find any key that might contain abundance
            print(f"⚠️ Standard keys not found, searching for abundance-like keys...")
            for k in adata_post.obsm.keys():
                if "abundance" in k.lower():
                    abundance_key = k
                    print(f"✅ Found alternative abundance key: {k}")
                    break

        if abundance_key is None:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"cell2location posterior does not contain expected abundance keys. "
                    f"Available keys: {list(adata_post.obsm.keys())}"
                ),
            )

        abundance = adata_post.obsm[abundance_key]  # DataFrame-like or ndarray
        print(f"📊 Abundance type: {type(abundance)}")
        # if hasattr(abundance, "shape"):
        #     print(f"📊 Abundance shape: {abundance.shape}")
        # if hasattr(abundance, "to_dataframe"):
        #     abundance_df = abundance.to_dataframe()
        # else:
        #     # Assume numpy array with cell types = columns, spots = rows
        #     abundance_df = pd.DataFrame(
        #         abundance,
        #         index=adata_post.obs_names,
        #         columns=ref_signatures.columns,
        #     )

        # ✅ 正确处理：如果已经是 DataFrame，就直接用
        if isinstance(abundance, pd.DataFrame):
            abundance_df = abundance.copy()
        # ✅ numpy array 才需要你指定列名（但列名要用 cell2location 的那套，而不是 ref_signatures.columns）
        elif isinstance(abundance, np.ndarray):
            # 这里更安全：优先用 adata_post.uns 里存的 mapping（不同版本可能没有）
            # 没有的话就退回 ref_signatures.columns
            col_names = list(ref_signatures.columns)
            abundance_df = pd.DataFrame(abundance, index=adata_post.obs_names, columns=col_names)
        else:
            # 有些版本可能是 AnnData 的 ArrayView / sparse 等
            abundance_df = pd.DataFrame(np.asarray(abundance), index=adata_post.obs_names, columns=list(ref_signatures.columns))

        print(f"📊 Original abundance_df shape: {abundance_df.shape}")
        print(f"📊 Original cell types: {list(abundance_df.columns)[:10]}...")  # Show first 10
        print(f"📊 Abundance sum (before NaN handling): {abundance_df.values.sum()}")
        print(f"📊 Abundance has NaN: {np.isnan(abundance_df.values).any()}")
        print(f"📊 Abundance has inf: {np.isinf(abundance_df.values).any()}")

        # Handle NaN/inf in abundance - replace with 0
        abundance_df = abundance_df.replace([np.inf, -np.inf], np.nan)
        # If everything is NaN or zeros, try falling back to another abundance key
        if np.isnan(abundance_df.values).all() or np.isclose(np.nansum(abundance_df.values), 0.0):
            fallback_key = "means_cell_abundance_w_sf" if abundance_key != "means_cell_abundance_w_sf" else None
            if fallback_key and fallback_key in adata_post.obsm:
                print(f"⚠️ Abundance from {abundance_key} is all NaN/zero, falling back to {fallback_key}")
                abundance = adata_post.obsm[fallback_key]
                if hasattr(abundance, "to_dataframe"):
                    abundance_df = abundance.to_dataframe()
                else:
                    abundance_df = pd.DataFrame(
                        abundance,
                        index=adata_post.obs_names,
                        columns=ref_signatures.columns,
                    )
                abundance_df = abundance_df.replace([np.inf, -np.inf], np.nan)

        abundance_df = abundance_df.fillna(0.0)

        print(f"📊 Abundance sum (after NaN handling): {abundance_df.values.sum():.2f}")

        # Strip known prefixes from column names to recover base cell type names.
        # Examples:
        #   "means_per_cluster_mu_fg_Astros_1" -> "Astros_1"
       #   "meanscell_abundance_w_sf_means_per_cluster_mu_fg_Astros_1" -> "Astros_1"
        strip_prefixes = [
            "meanscell_abundance_w_sf_",
            "means_cell_abundance_w_sf_",
            "means_per_cluster_mu_fg_",
        ]
        stripped_columns = []
        for col in abundance_df.columns:
            base = str(col)
            # Iteratively strip any known prefixes
            changed = True
            while changed:
                changed = False
                for p in strip_prefixes:
                    if base.startswith(p):
                        base = base[len(p):]
                        changed = True
            stripped_columns.append(base)
        
        # Create a mapping from original to stripped names
        col_mapping = dict(zip(abundance_df.columns, stripped_columns))
        abundance_df_stripped = abundance_df.rename(columns=col_mapping)
        
        print(f"📊 After stripping prefix, cell types: {list(abundance_df_stripped.columns)[:10]}...")

        # Keep a copy before merging, in case merge rules do not match any columns
        original_abundance_df = abundance_df_stripped.copy()

        # --------- Merge fine-grained cell types into coarse categories ----------
        # Current columns are fine-grained cell types from the reference (e.g. Astros_1, Ex_*, Inhib_*, ...)
        # For visualization, merge them into broader groups.
        merge_map: Dict[str, List[str]] = {
            "Astros": ["Astros_1", "Astros_2", "Astros_3"],
            "Excitatory": [c for c in abundance_df_stripped.columns if c.startswith("Ex_")],
            "Inhibitory": [c for c in abundance_df_stripped.columns if c.startswith("Inhib_")],
            "OPC": [c for c in abundance_df_stripped.columns if c.startswith("OPCs_")],
            "Oligodendrocyte": [c for c in abundance_df_stripped.columns if c.startswith("Oligos_")],
            "Microglia": ["Micro/Macro"],
        }

        # Debug: print what columns we're trying to match
        print(f"🔍 Merge map:")
        for new_name, cols in merge_map.items():
            valid_cols = [c for c in cols if c in abundance_df_stripped.columns]
            print(f"  {new_name}: {len(valid_cols)}/{len(cols)} matched - {valid_cols[:3]}...")

        merged_df = pd.DataFrame(index=abundance_df_stripped.index)
        for new_name, cols in merge_map.items():
            valid_cols = [c for c in cols if c in abundance_df_stripped.columns]
            if not valid_cols:
                # If no matching columns, fill zeros for this group
                merged_df[new_name] = 0.0
            else:
                merged_df[new_name] = abundance_df_stripped[valid_cols].sum(axis=1)

        merged_sum = merged_df.values.sum()
        print(f"📊 Merged abundance sum: {merged_sum:.2f}")

        # If merge produced all zeros (e.g. naming mismatch), fall back to original fine-grained types
        if np.isclose(merged_sum, 0.0):
            print("⚠️ Merge produced all zeros, falling back to original fine-grained cell types")
            abundance_df = original_abundance_df
        else:
            print(f"✅ Using merged coarse cell types: {list(merged_df.columns)}")
            abundance_df = merged_df

        # Normalize rows to sum to 1 -> proportions per spot (coarse cell types)
        abundance_values = abundance_df.values.astype(float)
        # Replace NaN / inf with 0 before normalization to avoid JSON issues later
        abundance_values = np.nan_to_num(abundance_values, nan=0.0, posinf=0.0, neginf=0.0)

        row_sums = abundance_values.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        proportions_arr = abundance_values / row_sums
        # Final safety: ensure no NaN/inf in proportions
        proportions_arr = np.nan_to_num(proportions_arr, nan=0.0, posinf=0.0, neginf=0.0)
        
        # Debug: check proportions
        print(f"📊 Proportions shape: {proportions_arr.shape}")
        print(f"📊 Proportions sum per spot (first 5): {proportions_arr[:5].sum(axis=1)}")
        print(f"📊 Max proportion per cell type: {proportions_arr.max(axis=0)}")
        print(f"📊 Non-zero proportions count: {(proportions_arr > 0.01).sum()}")
        
        proportions = proportions_arr.tolist()

        cell_types = abundance_df.columns.astype(str).tolist()
        spots = abundance_df.index.astype(str).tolist()
        
        print(f"📊 Returning {len(cell_types)} cell types, {len(spots)} spots")
        print(f"📊 Cell types: {cell_types[:5]}...")  # Show first 5
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error converting cell2location output to proportions: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert cell2location output to proportion matrix: {e!s}",
        )

    # Build hierarchy for icicle chart: include both merged (coarse) and fine-grained cell types
    try:
        hierarchy = []
        merge_map_used = {}

        # Also calculate fine-grained proportions for filtering
        original_proportions = None
        original_cell_types = None

        # Add merged (coarse) cell types as parent nodes
        if merged_sum > 0 and not np.isclose(merged_sum, 0.0):
            # We used merged types - build hierarchy
            for coarse_name in merged_df.columns:
                hierarchy.append({
                    "name": coarse_name,
                    "type": "coarse",
                    "children": []
                })
                merge_map_used[coarse_name] = []

            # Map fine-grained types to their parent coarse types
            for new_name, cols in merge_map.items():
                valid_cols = [c for c in cols if c in original_abundance_df.columns]
                if valid_cols:
                    merge_map_used[new_name] = valid_cols
                    node = next((n for n in hierarchy if n["name"] == new_name), None)
                    if node:
                        for fine_name in valid_cols:
                            node["children"].append({
                                "name": fine_name,
                                "type": "fine",
                                "parent": new_name
                            })

            # Calculate fine-grained proportions from original data
            original_abundance_values = original_abundance_df.values.astype(float)
            original_abundance_values = np.nan_to_num(original_abundance_values, nan=0.0, posinf=0.0, neginf=0.0)
            original_row_sums = original_abundance_values.sum(axis=1, keepdims=True)
            original_row_sums[original_row_sums == 0] = 1.0
            original_proportions_arr = original_abundance_values / original_row_sums
            original_proportions_arr = np.nan_to_num(original_proportions_arr, nan=0.0, posinf=0.0, neginf=0.0)
            original_proportions = original_proportions_arr.tolist()
            original_cell_types = original_abundance_df.columns.astype(str).tolist()
        else:
            # We fell back to fine-grained types, no hierarchy
            for fine_name in original_abundance_df.columns:
                hierarchy.append({
                    "name": fine_name,
                    "type": "fine",
                    "children": []
                })
            original_proportions = proportions
            original_cell_types = cell_types

        return {
            "cell_types": cell_types,
            "spots": spots,
            "proportions": proportions,
            "hierarchy": hierarchy,
            "merge_map": merge_map_used,
            "original_cell_types": original_cell_types,
            "original_proportions": original_proportions,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Deconvolution failed: {str(e)}",
        )

class GeminiAnalyzeRequest(BaseModel):
    analysis_type: str
    analysis_data: Any  # Can be dict, list, or any JSON-serializable structure
    slice_id: Optional[str] = None
    cluster_result_id: Optional[str] = None
    additional_context: Optional[str] = None

@app.post("/gemini-analyze")
def gemini_analyze_endpoint(request: GeminiAnalyzeRequest):
    """
    Analyze downstream analysis results using OpenAI.
    
    This endpoint takes analysis results from various downstream analyses (SVG, spatial communication,
    DEG, deconvolution, neighborhood) and uses OpenAI to provide biological interpretation and insights.
    
    Returns:
    {
        "analysis": "AI-generated analysis text",
        "analysis_type": "svg|spatial|deg|deconvolution|neighborhood",
        "status": "success|error",
        "error": "error message if status is error"
    }
    """
    try:
        if request.analysis_type not in ["svg", "spatial", "deg", "deconvolution", "neighborhood"]:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid analysis_type: {request.analysis_type}. Must be one of: svg, spatial, deg, deconvolution, neighborhood"
            )
        
        # Call LLM analysis
        interpretation = analyze_downstream_results(
            analysis_type=request.analysis_type,
            analysis_data=request.analysis_data,
            slice_id=request.slice_id,
            cluster_result_id=request.cluster_result_id,
            additional_context=request.additional_context
        )
        
        return {
            "analysis": interpretation,
            "analysis_type": request.analysis_type,
            "status": "success"
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in LLM analysis: {e}")
        return {
            "analysis": "",
            "analysis_type": request.analysis_type,
            "status": "error",
            "error": str(e)
        }

if RPY2_AVAILABLE and robjects is not None:
    try:
        print("rpy2 R version:", robjects.r("R.version.string"))
        print("rpy2 R home:", robjects.r("R.home()"))
    except Exception as _e:
        # Non-fatal: the API can still run for endpoints that don't require R.
        print(f"⚠️ rpy2 is installed but R is not usable yet: {_e}")

def get_cluster_color_palette() -> list:
    """Provide a stable palette (up to 20 distinct colors)."""
    return [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#393b79", "#637939", "#8c6d31", "#843c39", "#7b4173",
        "#5254a3", "#9c9ede", "#6b6ecf", "#9c755f", "#cedb9c"
    ]

def build_cluster_color_mapping(cluster_names: list) -> dict:
    palette = get_cluster_color_palette()
    mapping = {}
    for idx, name in enumerate(cluster_names):
        color_hex = palette[idx % len(palette)]
        rgb = tuple(int(color_hex[i:i+2], 16) for i in (1, 3, 5))
        mapping[str(name)] = f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"
    return mapping

@app.get("/cluster-color-mapping")
def get_cluster_color_mapping(
    slice_id: str = Query(...),
    cluster_result_id: str = "default"
):
    """Return consistent colors for each cluster (shared with frontend)."""
    table_name = f"spot_cluster_{slice_id}"
    query = text(
        f"SELECT DISTINCT cluster FROM `{table_name}` WHERE cluster_result_id = :cluster_result_id"
    )

    with engine.connect() as conn:
        rows = conn.execute(query, {"cluster_result_id": cluster_result_id}).fetchall()

    if not rows:
        return {"color_mapping": {}, "clusters": []}

    def sort_key(name: str):
        s = str(name)
        try:
            return float(s)
        except ValueError:
            return float("inf")

    clusters = sorted((str(r[0]) for r in rows), key=sort_key)
    color_mapping = build_cluster_color_mapping(clusters)

    return {
        "clusters": clusters,
        "color_mapping": color_mapping
    }

def fetch_cluster_data(slice_id: str, cluster_result_id: str) -> pd.DataFrame:
    table_name = f"spot_cluster_{slice_id}"
    query = text(
        f"SELECT barcode, cluster, emb FROM `{table_name}` WHERE cluster_result_id = :cluster_result_id"
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"cluster_result_id": cluster_result_id}).fetchall()
    if not rows:
        return pd.DataFrame(columns=["barcode", "cluster", "emb"])
    return pd.DataFrame(rows, columns=["barcode", "cluster", "emb"])

def fetch_cluster_labels(slice_id: str, cluster_result_id: str) -> dict:
    df = fetch_cluster_data(slice_id, cluster_result_id)
    if df.empty:
        return {}
    return dict(zip(df["barcode"], df["cluster"].astype(str)))


def load_multi_result_label_arrays(slice_id: str, adata, cluster_result_ids: List[str]) -> List[np.ndarray]:
    """
    从数据库加载多份聚类结果的标签，按 adata.obs 顺序对齐为数组列表，供多结果分布 Refine 使用。
    若某 result 中缺少某 barcode，则用 adata.obs['domain'] 填充。
    """
    out = []
    for rid in cluster_result_ids:
        label_map = fetch_cluster_labels(slice_id, rid)
        arr = []
        for bc in adata.obs_names:
            val = label_map.get(bc)
            if val is None and "domain" in adata.obs.columns:
                val = str(adata.obs.loc[bc, "domain"])
            if val is None:
                val = "unknown"
            arr.append(val)
        out.append(np.array(arr))
    return out


def apply_cluster_labels(adata_obj, slice_id: str, cluster_result_id: str, cluster_field: str, overrides: dict = None) -> None:
    df = fetch_cluster_data(slice_id, cluster_result_id)
    if df.empty:
        print(f"⚠️ 未在数据库中找到 cluster_result_id={cluster_result_id} 的聚类信息，保持原始值")
        return

    label_map = dict(zip(df["barcode"], df["cluster"].astype(str)))
    if overrides:
        for bc, new_cluster in overrides.items():
            if bc and new_cluster is not None:
                label_map[str(bc).strip()] = str(new_cluster).strip()

    mapped = []
    fallback_series = None
    if cluster_field in adata_obj.obs:
        fallback_series = adata_obj.obs[cluster_field].astype(str)

    for barcode in adata_obj.obs_names:
        value = label_map.get(barcode)
        if value is None and fallback_series is not None:
            value = fallback_series.loc[barcode]
        if value is None:
            value = "unknown"
        mapped.append(str(value))

    adata_obj.obs[cluster_field] = mapped
    adata_obj.obs["cluster"] = mapped
    adata_obj.obs["domain"] = pd.Categorical(mapped)

    emb_series = df.set_index("barcode")["emb"]
    if not emb_series.dropna().empty:
        first = emb_series.dropna().iloc[0]
        dim = len(first.split(",")) if isinstance(first, str) else 0
        if dim > 0:
            emb_matrix = np.zeros((adata_obj.n_obs, dim), dtype=float)
            has_value = False
            for idx, barcode in enumerate(adata_obj.obs_names):
                emb_str = emb_series.get(barcode)
                if emb_str:
                    vec = np.fromstring(emb_str, sep=",")
                    if vec.size == dim:
                        emb_matrix[idx] = vec
                        has_value = True
            if has_value:
                adata_obj.obsm["emb"] = emb_matrix

def align_cluster_labels(
    labels_source: dict, 
    labels_target: dict, 
    method: str = "hungarian"
) -> dict:
    """
    Align cluster labels from source to target using Hungarian algorithm.
    
    Parameters
    ----------
    labels_source : dict
        Dictionary mapping barcode to cluster label (source clustering)
    labels_target : dict
        Dictionary mapping barcode to cluster label (target/reference clustering)
    method : str
        Alignment method. Currently only "hungarian" is supported.
        
    Returns
    -------
    dict
        Mapping from source labels to aligned target labels
    """
    if method != "hungarian":
        raise ValueError(f"Unsupported alignment method: {method}")
    
    # Get common barcodes
    common_barcodes = set(labels_source.keys()) & set(labels_target.keys())
    if not common_barcodes:
        raise ValueError("No common barcodes between source and target labels")
    
    # Get unique labels
    source_labels = sorted(set(labels_source.values()))
    target_labels = sorted(set(labels_target.values()))
    
    # Build confusion matrix
    # Rows: source labels, Columns: target labels
    confusion_matrix = np.zeros((len(source_labels), len(target_labels)), dtype=int)
    source_label_to_idx = {label: idx for idx, label in enumerate(source_labels)}
    target_label_to_idx = {label: idx for idx, label in enumerate(target_labels)}
    
    for barcode in common_barcodes:
        source_label = labels_source[barcode]
        target_label = labels_target[barcode]
        if source_label in source_label_to_idx and target_label in target_label_to_idx:
            i = source_label_to_idx[source_label]
            j = target_label_to_idx[target_label]
            confusion_matrix[i, j] += 1
    
    # Use Hungarian algorithm to find optimal assignment
    # We want to maximize overlap, so we use negative of confusion matrix as cost
    cost_matrix = -confusion_matrix
    row_indices, col_indices = linear_sum_assignment(cost_matrix)
    
    # Build label mapping
    label_mapping = {}
    for i, j in zip(row_indices, col_indices):
        source_label = source_labels[i]
        target_label = target_labels[j]
        label_mapping[source_label] = target_label
    
    # Handle unmapped source labels (assign to nearest target label or create new)
    unmapped_source = set(source_labels) - set(label_mapping.keys())
    for source_label in unmapped_source:
        # Find target label with maximum overlap
        source_idx = source_label_to_idx[source_label]
        best_target_idx = np.argmax(confusion_matrix[source_idx, :])
        label_mapping[source_label] = target_labels[best_target_idx]
    
    return label_mapping

def apply_label_alignment(
    slice_id: str,
    source_cluster_result_id: str,
    target_cluster_result_id: str,
    new_cluster_result_id: Optional[str] = None
) -> dict:
    """
    Align labels from source clustering to target clustering and save the result.
    
    Parameters
    ----------
    slice_id : str
        Slice ID
    source_cluster_result_id : str
        Source clustering result ID to align
    target_cluster_result_id : str
        Target/reference clustering result ID
    new_cluster_result_id : str, optional
        If provided, save aligned labels to this new cluster_result_id.
        If None, update the source cluster_result_id in place.
        
    Returns
    -------
    dict
        Alignment statistics and label mapping
    """
    # Fetch labels
    source_labels = fetch_cluster_labels(slice_id, source_cluster_result_id)
    target_labels = fetch_cluster_labels(slice_id, target_cluster_result_id)
    
    if not source_labels:
        raise HTTPException(
            status_code=404, 
            detail=f"Source cluster result '{source_cluster_result_id}' not found"
        )
    if not target_labels:
        raise HTTPException(
            status_code=404, 
            detail=f"Target cluster result '{target_cluster_result_id}' not found"
        )
    
    # Align labels
    label_mapping = align_cluster_labels(source_labels, target_labels)
    
    # Apply mapping to source labels
    aligned_labels = {}
    for barcode, source_label in source_labels.items():
        aligned_labels[barcode] = label_mapping.get(source_label, source_label)
    
    # Save aligned labels
    result_id = new_cluster_result_id or source_cluster_result_id
    table_name = f"spot_cluster_{slice_id}"
    metadata = MetaData()
    metadata.reflect(bind=engine)
    spot_cluster = metadata.tables[table_name]
    
    # Get original cluster data
    source_df = fetch_cluster_data(slice_id, source_cluster_result_id)
    
    # Fetch full records from database to preserve x, y, and other fields
    table_name = f"spot_cluster_{slice_id}"
    query = text(
        f"SELECT * FROM `{table_name}` WHERE cluster_result_id = :cluster_result_id"
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"cluster_result_id": source_cluster_result_id}).fetchall()
        if rows:
            source_full_df = pd.DataFrame(rows)
            source_full_df.columns = [col[1] for col in conn.execute(text(f"PRAGMA table_info(`{table_name}`)")).fetchall()]
        else:
            source_full_df = pd.DataFrame()
    
    with engine.begin() as conn:
        for _, row in source_df.iterrows():
            barcode = row["barcode"]
            original_label = row["cluster"]
            aligned_label = aligned_labels.get(barcode, original_label)
            
            # Get full row data if available
            full_row = None
            if not source_full_df.empty:
                full_row_data = source_full_df[source_full_df["barcode"] == barcode]
                if not full_row_data.empty:
                    full_row = full_row_data.iloc[0]
            
            # Check if record exists
            existing = conn.execute(
                select(spot_cluster).where(
                    spot_cluster.c.barcode == barcode,
                    spot_cluster.c.cluster_result_id == result_id
                )
            ).fetchone()
            
            if existing:
                # Update existing record - only update cluster field
                conn.execute(
                    update(spot_cluster)
                    .where(
                        spot_cluster.c.barcode == barcode,
                        spot_cluster.c.cluster_result_id == result_id
                    )
                    .values(cluster=aligned_label)
                )
            else:
                # Insert new record - copy from source if available
                if full_row is not None:
                    insert_values = {
                        "barcode": barcode,
                        "cluster_result_id": result_id,
                        "cluster": aligned_label,
                    }
                    # Copy other fields if they exist
                    for col in ["x", "y", "emb", "n_count_spatial", "n_feature_spatial", 
                               "percent_mito", "percent_ribo"]:
                        if col in full_row and pd.notna(full_row[col]):
                            insert_values[col] = full_row[col]
                    
                    conn.execute(insert(spot_cluster).values(**insert_values))
                else:
                    # Fallback: minimal insert
                    conn.execute(
                        insert(spot_cluster).values(
                            barcode=barcode,
                            cluster_result_id=result_id,
                            cluster=aligned_label,
                            emb=row.get("emb", "")
                        )
                    )
    
    # Calculate alignment statistics
    common_barcodes = set(source_labels.keys()) & set(target_labels.keys())
    alignment_accuracy = 0.0
    if common_barcodes:
        matches = sum(
            1 for barcode in common_barcodes 
            if aligned_labels.get(barcode) == target_labels.get(barcode)
        )
        alignment_accuracy = matches / len(common_barcodes)
    
    # ✅ Generate and update preview plot after alignment
    plot_path = None
    try:
        plot_path = save_cluster_plot(slice_id, result_id)
        if plot_path:
            # Update cluster_method table with new plot_path
            metadata.reflect(bind=engine, only=["cluster_method"])
            cluster_method = metadata.tables["cluster_method"]
            with engine.begin() as conn:
                # Check if record exists
                existing = conn.execute(
                    select(cluster_method).where(
                        cluster_method.c.slice_id == slice_id,
                        cluster_method.c.cluster_result_id == result_id
                    )
                ).fetchone()
                
                if existing:
                    # Update existing record - 只更新 plot_path，保留其他字段（包括 metrics）
                    conn.execute(
                        update(cluster_method)
                        .where(
                            cluster_method.c.slice_id == slice_id,
                            cluster_method.c.cluster_result_id == result_id
                        )
                        .values(plot_path=plot_path)
                    )
                else:
                    # Insert new record if it doesn't exist
                    # Try to copy metadata from source if available (包括 metrics)
                    source_metadata = conn.execute(
                        select(cluster_method).where(
                            cluster_method.c.slice_id == slice_id,
                            cluster_method.c.cluster_result_id == source_cluster_result_id
                        )
                    ).fetchone()
                    
                    if source_metadata:
                        # 提取所有字段，包括 metrics
                        source_method = source_metadata.method if hasattr(source_metadata, 'method') else (source_metadata[2] if len(source_metadata) > 2 else None)
                        source_n_clusters = source_metadata.n_clusters if hasattr(source_metadata, 'n_clusters') else (source_metadata[3] if len(source_metadata) > 3 else None)
                        source_epoch = source_metadata.epoch if hasattr(source_metadata, 'epoch') else (source_metadata[4] if len(source_metadata) > 4 else None)
                        source_chao = source_metadata.chao if hasattr(source_metadata, 'chao') else (source_metadata[6] if len(source_metadata) > 6 else None)
                        source_silhouette = source_metadata.silhouette if hasattr(source_metadata, 'silhouette') else (source_metadata[7] if len(source_metadata) > 7 else None)
                        source_pas = source_metadata.pas if hasattr(source_metadata, 'pas') else (source_metadata[8] if len(source_metadata) > 8 else None)
                        source_morans_i = source_metadata.morans_i if hasattr(source_metadata, 'morans_i') else (source_metadata[9] if len(source_metadata) > 9 else None)
                        
                        insert_values = {
                            "slice_id": slice_id,
                            "cluster_result_id": result_id,
                            "result_name": f"Aligned from {source_cluster_result_id}",
                            "plot_path": plot_path,
                            "updated_at": func.current_timestamp()
                        }
                        
                        if source_method is not None:
                            insert_values["method"] = source_method
                        if source_n_clusters is not None:
                            insert_values["n_clusters"] = source_n_clusters
                        if source_epoch is not None:
                            insert_values["epoch"] = source_epoch
                        # 复制 metrics 字段
                        if source_chao is not None:
                            insert_values["chao"] = source_chao
                        if source_silhouette is not None:
                            insert_values["silhouette"] = source_silhouette
                        if source_pas is not None:
                            insert_values["pas"] = source_pas
                        if source_morans_i is not None:
                            insert_values["morans_i"] = source_morans_i
                        
                        conn.execute(
                            insert(cluster_method).values(**insert_values)
                        )
                    else:
                        conn.execute(
                            insert(cluster_method).values(
                                slice_id=slice_id,
                                cluster_result_id=result_id,
                                result_name=f"Aligned from {source_cluster_result_id}",
                                plot_path=plot_path,
                                updated_at=func.current_timestamp()
                            )
                        )
            print(f"✅ Preview plot updated: {plot_path}")
    except Exception as e:
        print(f"⚠️ Failed to update preview plot: {e}")
        # Don't fail the whole operation if plot generation fails
    
    return {
        "label_mapping": label_mapping,
        "alignment_accuracy": alignment_accuracy,
        "common_barcodes": len(common_barcodes),
        "result_id": result_id,
        "plot_path": plot_path
    }

@app.post("/align-cluster-labels")
def align_cluster_labels_endpoint(
    slice_id: str = Query(...),
    source_cluster_result_id: str = Query(..., description="Source clustering result ID to align"),
    target_cluster_result_id: str = Query(..., description="Target/reference clustering result ID"),
    new_cluster_result_id: Optional[str] = Query(None, description="Optional: save to new cluster_result_id instead of updating source")
):
    """
    Align cluster labels from source clustering to target clustering using Hungarian algorithm.
    
    This endpoint uses the Hungarian algorithm to find the optimal mapping between
    cluster labels from two different clustering results, ensuring that similar clusters
    are assigned the same labels for easier comparison.
    
    Parameters
    ----------
    slice_id : str
        Slice ID
    source_cluster_result_id : str
        Source clustering result ID to align
    target_cluster_result_id : str
        Target/reference clustering result ID (labels will be aligned to this)
    new_cluster_result_id : str, optional
        If provided, save aligned labels to this new cluster_result_id.
        If None, update the source cluster_result_id in place.
        
    Returns
    -------
    dict
        Alignment statistics including label mapping and accuracy
    """
    try:
        result = apply_label_alignment(
            slice_id=slice_id,
            source_cluster_result_id=source_cluster_result_id,
            target_cluster_result_id=target_cluster_result_id,
            new_cluster_result_id=new_cluster_result_id
        )
        return {
            "success": True,
            "message": f"Labels aligned successfully. Accuracy: {result['alignment_accuracy']:.2%}",
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error aligning labels: {str(e)}")

@app.post("/align-multiple-cluster-labels")
def align_multiple_cluster_labels_endpoint(
    slice_id: str = Query(...),
    target_cluster_result_id: str = Query(..., description="Reference clustering result ID"),
    source_cluster_result_ids: str = Query(..., description="Comma-separated list of source cluster_result_ids to align")
):
    """
    Align multiple cluster results to a reference clustering result.
    
    Parameters
    ----------
    slice_id : str
        Slice ID
    target_cluster_result_id : str
        Reference clustering result ID
    source_cluster_result_ids : str
        Comma-separated list of source cluster_result_ids to align
        
    Returns
    -------
    dict
        Results for each alignment operation
    """
    source_ids = [sid.strip() for sid in source_cluster_result_ids.split(",") if sid.strip()]
    
    if not source_ids:
        raise HTTPException(status_code=400, detail="No source cluster_result_ids provided")
    
    results = []
    for source_id in source_ids:
        try:
            result = apply_label_alignment(
                slice_id=slice_id,
                source_cluster_result_id=source_id,
                target_cluster_result_id=target_cluster_result_id,
                new_cluster_result_id=None  # Update in place
            )
            results.append({
                "source_cluster_result_id": source_id,
                "success": True,
                **result
            })
        except Exception as e:
            results.append({
                "source_cluster_result_id": source_id,
                "success": False,
                "error": str(e)
            })
    
    return {
        "success": True,
        "results": results,
        "summary": {
            "total": len(results),
            "succeeded": sum(1 for r in results if r.get("success", False)),
            "failed": sum(1 for r in results if not r.get("success", False))
        }
    }
