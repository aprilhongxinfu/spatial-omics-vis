### ST-Refinery

This repository contains a full stack system for **evaluating and refining
clustering results on spatial transcriptomics (ST) data**. ST-Refinery helps
researchers compare clustering runs, inspect global/local patterns, and make
targeted local edits to clusters. It consists of:

- **`omics-backend/`** – Python / FastAPI backend for data loading, clustering,
  database management and optional LLM-based downstream analysis.
- **`omics-web/`** – Svelte + Vite frontend for visualizing slices, clusters,
  quality-control metrics and comparing clustering results.

---

### Backend (`omics-backend`)

The backend is a FastAPI application that:

- Loads 10x Visium-style data from `omics-backend/data/<slice_id>/`.
- Computes QC metrics (`nCount_Spatial`, `nFeature_Spatial`, mito/ribo, etc.).
- Runs multiple clustering methods (GraphST, SEDR, SpaGCN, …) and stores results
  in a SQLite database (`omics_data.db` by default).
- Exposes REST APIs under `http://localhost:3538` for:
  - slice loading / switching
  - clustering and re-clustering
  - QC summaries and per-spot metrics
  - downstream analyses (SVG, spatial communication, deconvolution, etc.)
  - optional LLM analysis of downstream results.

See `omics-backend/readme.md` for detailed environment setup and run
instructions. In short:

```bash
cd omics-backend
conda env create -f environment.yml
conda activate omics-backend
uvicorn main:app --host 0.0.0.0 --port 3538 --reload
```

The interactive API docs are available at:

```text
http://localhost:3538/docs
```

---

### Frontend (`omics-web`)

The frontend is a Svelte app that talks to the backend via `/api/*` and
provides:

- Slice overview and spatial scatter plots with cluster colors.
- UMAP embeddings and QC plots (including violin plots).
- Cluster management and interactive cluster editing.
- Cluster result comparison, including circular cluster–spot comparison and
  stability-based views.

See `omics-web/README.md` for details. Basic workflow:

```bash
cd omics-web
npm install
npm run dev
```

The dev server runs at:

```text
http://localhost:5173
```

Make sure the backend is running on `http://localhost:3538` so that the
frontend can reach the API.
