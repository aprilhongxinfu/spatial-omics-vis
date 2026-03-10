## Backend setup (`omics-backend`)

This backend is a FastAPI service for interactive spatial transcriptomics analysis.

The project **uses conda as the default and only supported way** to manage the
Python/R environment. `environment.yml` is the single source of truth.

### 1. Create and activate the conda environment

From the `omics-backend/` directory:

```bash
conda env create -f environment.yml
conda activate omics-backend
```

If you later change `environment.yml` and want to update an existing env:

```bash
conda env update -f environment.yml --prune
```


### 2. Data layout under `data/`

Raw data for each slice lives under `data/<slice_id>/`. A typical 10x Visium
layout looks like:

```text
data/
└── 151673/
    ├── filtered_feature_bc_matrix.h5
    ├── info.json
    ├── metadata.tsv
    ├── scRNA.h5ad
    └── spatial/
        ├── full_image.tif
        ├── scalefactors_json.json
        ├── tissue_hires_image.png
        ├── tissue_lowres_image.png
        └── tissue_positions_list.csv
```

`info.json` stores basic metadata, for example:

```json
{
  "tissue": "Human DLPFC (dorsolateral prefrontal cortex)",
  "platform": "10x Genomics Visium",
  "slice_id": "151673",
  "spot_diameter_um": 55,
  "spot_spacing_um": 100
}
```

Only the `data/` directory itself is tracked in git; the actual data files and
sub‑directories are ignored so you can keep your own local datasets.

### 3. Database configuration

By default the backend uses a local **SQLite** database file:

```python
engine = create_engine("sqlite:///omics_data.db", connect_args={"check_same_thread": False})
```

The file `omics_data.db` will be created automatically on first use, and is
**not** tracked by git.

There is optional support for MySQL if you edit the engine configuration in
`main.py` and provide environment variables via a `.env` file in
`omics-backend/`:

```bash
DB_USER=YOUR_DB_USER
DB_PASSWORD=YOUR_DB_PASSWORD
DB_HOST=YOUR_DB_HOST
DB_NAME=YOUR_DB_NAME
```

### 4. Run the backend

From `omics-backend/` (with the conda env activated):

```bash
uvicorn main:app --host 0.0.0.0 --port 3538 --reload
```

Interactive API docs (Swagger UI) will be available at:

```text
http://localhost:3538/docs
```

If you want to keep the server running in the background on a remote machine,
you can use `tmux`:

```bash
tmux new -s uvicorn_server
uvicorn main:app --host 0.0.0.0 --port 3538 --reload
```

Then:

- Detach: `Ctrl + B`, then `D`
- Re‑attach later: `tmux attach -t uvicorn_server`