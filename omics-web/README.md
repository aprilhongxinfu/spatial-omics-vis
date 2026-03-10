
### Frontend setup (`omics-web`)

This is the Svelte-based web frontend for the spatial omics viewer. It talks to
the backend via the `/api` prefix (e.g. `/api/plot-data`, `/api/run-clustering`).

### 1. Prerequisites

Make sure you have a recent [Node.js](https://nodejs.org/) (recommended **≥ 16**)
and npm installed:

```bash
node -v
npm -v
```

### 2. Install dependencies

From the `omics-web/` directory:

```bash
npm install
```

### 3. Run the dev server

Still in `omics-web/`:

```bash
npm run dev
```

Or bind to all interfaces (useful when accessing from another machine):

```bash
npm run dev -- --host 0.0.0.0
```

By default Vite will start the dev server at:

```text
http://localhost:5173
```

Make sure the backend (`omics-backend`) is running on port `3538` so that the
frontend can reach the API at `/api/*`.