import os
from pathlib import Path


def configure_r_home() -> str | None:
    """
    Configure R-related environment variables for rpy2.

    Why:
    - Some files in this repo previously hard-coded a Linux-only R_HOME.
    - On macOS (and in general), we should auto-detect R inside the active conda env.

    Returns:
    - The resolved R_HOME path if found, otherwise None.
    """
    # If user already set it, trust that.
    existing = os.environ.get("R_HOME")
    if existing:
        return existing

    candidates: list[Path] = []

    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        candidates.append(Path(conda_prefix) / "lib" / "R")

    # Common macOS locations (Homebrew)
    candidates.append(Path("/opt/homebrew/opt/r/lib/R"))
    candidates.append(Path("/usr/local/opt/r/lib/R"))

    for r_home in candidates:
        rscript = r_home / "bin" / "Rscript"
        if rscript.exists():
            os.environ["R_HOME"] = str(r_home)
            # Ensure R binaries are on PATH for subprocess detection.
            os.environ["PATH"] = f"{r_home / 'bin'}:{os.environ.get('PATH', '')}"

            # macOS uses dyld; LD_LIBRARY_PATH is often ignored under SIP.
            # DYLD_FALLBACK_LIBRARY_PATH is safer for locating libR.dylib.
            lib_path = str(r_home / "lib")
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
                f"{lib_path}:{os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '')}"
            )
            return str(r_home)

    return None


