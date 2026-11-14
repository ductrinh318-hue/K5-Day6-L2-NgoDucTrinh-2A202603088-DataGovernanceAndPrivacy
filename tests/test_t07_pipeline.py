from pathlib import Path
import pandas as pd
import pytest

from src import main as runner


def test_t07_pipeline_e2e_success(tmp_path: Path):
    """Test normal end-to-end execution of T07 pipeline on offline fixtures."""
    rc = runner.main([
        "--config", "configs/lab.yaml",
        "--track", "t07",
        "--input", "fixtures/t07_redaction",
        "--output", str(tmp_path),
        "--offline",
        "--seed", "42",
    ])
    assert rc == 0

    pred_file = tmp_path / "redactions.csv"
    queue_file = tmp_path / "review_queue.csv"
    assert pred_file.exists()
    assert queue_file.exists()

    df = pd.read_csv(pred_file)
    for col in ["image_id", "x", "y", "w", "h"]:
        assert col in df.columns
    assert (df["w"] > 0).all()
    assert (df["h"] > 0).all()


def test_t07_review_missing_reviewer_fails(tmp_path: Path):
    """Edge/failure case: reviewed queue without reviewer name must fail loudly."""
    rc1 = runner.main([
        "--config", "configs/lab.yaml",
        "--track", "t07",
        "--input", "fixtures/t07_redaction",
        "--output", str(tmp_path),
        "--offline",
        "--seed", "42",
    ])
    assert rc1 == 0

    q = pd.read_csv(tmp_path / "review_queue.csv", dtype=str)
    q.loc[0, "reviewer_decision"] = "approve"
    q.loc[0, "reviewer"] = ""
    q.to_csv(tmp_path / "review_queue_reviewed.csv", index=False)

    rc2 = runner.main([
        "--config", "configs/lab.yaml",
        "--track", "t07",
        "--input", "fixtures/t07_redaction",
        "--output", str(tmp_path),
        "--offline",
        "--seed", "42",
    ])
    assert rc2 != 0
