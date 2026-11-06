"""What every domain's catalog loader raises for a dataset that is not prepared yet."""

from pathlib import Path


class DataFileMissingError(FileNotFoundError):
    def __init__(self, path: Path, prepare: str) -> None:
        """`prepare` is the script module that writes the file, e.g. `scripts.prepare_data`."""
        super().__init__(
            f"Data file not found: {path}. Prepare the dataset with "
            f"`uv run python -m {prepare}` (see README)."
        )
