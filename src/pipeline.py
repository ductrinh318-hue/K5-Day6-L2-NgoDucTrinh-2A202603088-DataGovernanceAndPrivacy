"""PoC Pipeline for Track T07 â€” Automatic Face/Plate Redaction + Review Queue."""
from __future__ import annotations

import cv2
import numpy as np
import pandas as pd

from .common.contact_sheet import render_contact_sheet
from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.manifest import verify_manifest
from .common.review import load_review_decisions, make_review_queue, write_review_queue
from .metrics.tracks import get_track


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")

    images_path = ctx.input_dir / "images.csv"
    if not images_path.exists():
        raise FileNotFoundError(f"Missing required input table: {images_path}")

    images_df = pd.read_csv(images_path)
    return {
        "manifest": pd.read_csv(manifest) if manifest.exists() else None,
        "images": images_df,
    }


# ------------------------------------------------ Helper: Detectors & Blur
def _detect_faces(gray: np.ndarray, bgr: np.ndarray, pad_ratio: float = 0.15) -> list[dict]:
    """OpenCV 5.0 compatible synthetic cartoon/human face detector."""
    h_img, w_img = gray.shape[:2]
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    blurred = cv2.GaussianBlur(enhanced, (5, 5), 0)

    results = []
    # 1. Detect circular/oval head/face blobs via HoughCircles
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=24,
        param1=80,
        param2=26,
        minRadius=8,
        maxRadius=65,
    )
    if circles is not None:
        for (cx, cy, r) in np.round(circles[0, :]).astype("int"):
            x = max(0, cx - r)
            y = max(0, cy - r)
            w = min(w_img - x, 2 * r)
            h = min(h_img - y, 2 * r)
            if w < 12 or h < 12:
                continue
            roi = enhanced[y : y + h, x : x + w]
            internal_std = float(roi.std())
            if internal_std < 18.0:
                continue
            conf = min(0.93, 0.50 + (internal_std / 120.0))
            px = int(round(w * pad_ratio))
            py = int(round(h * pad_ratio))
            nx = max(0, x - px)
            ny = max(0, y - py)
            nw = min(w_img - nx, w + 2 * px)
            nh = min(h_img - ny, h + 2 * py)
            results.append({
                "type": "face",
                "x": nx,
                "y": ny,
                "w": nw,
                "h": nh,
                "conf": round(float(conf), 4),
            })

    # 2. Supplement with skin/warm-tone & oval contour detection in YCrCb
    ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    skin_mask = cv2.inRange(
        ycrcb,
        np.array([35, 133, 77], dtype=np.uint8),
        np.array([245, 178, 132], dtype=np.uint8),
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_OPEN, kernel)
    skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(skin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w < 14 or h < 14 or w > 140 or h > 160:
            continue
        aspect = w / float(h)
        if not (0.65 <= aspect <= 1.35):
            continue
        roi = enhanced[y : y + h, x : x + w]
        if float(roi.std()) < 16.0:
            continue

        cx_new, cy_new = x + w / 2.0, y + h / 2.0
        is_dup = any(
            abs((b["x"] + b["w"] / 2.0) - cx_new) < max(w, b["w"]) * 0.5
            and abs((b["y"] + b["h"] / 2.0) - cy_new) < max(h, b["h"]) * 0.5
            for b in results
        )
        if is_dup:
            continue

        px = int(round(w * pad_ratio))
        py = int(round(h * pad_ratio))
        nx = max(0, x - px)
        ny = max(0, y - py)
        nw = min(w_img - nx, w + 2 * px)
        nh = min(h_img - ny, h + 2 * py)
        results.append({
            "type": "face",
            "x": nx,
            "y": ny,
            "w": nw,
            "h": nh,
            "conf": 0.72,
        })

    return results


def _detect_plates(bgr: np.ndarray, gray: np.ndarray, pad_ratio: float = 0.12) -> list[dict]:
    """OpenCV 5.0 compatible license plate detector using morphological gradient & aspect ratio."""
    h_img, w_img = gray.shape[:2]
    rect_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 5))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, rect_kernel)
    grad_x = cv2.Sobel(tophat, ddepth=cv2.CV_32F, dx=1, dy=0, ksize=3)
    grad_x = np.absolute(grad_x)
    max_val = float(grad_x.max()) if grad_x.size else 0.0
    if max_val > 0:
        grad_x = (255 * (grad_x / max_val)).astype("uint8")
    else:
        grad_x = grad_x.astype("uint8")

    grad_x = cv2.GaussianBlur(grad_x, (5, 5), 0)
    grad_x = cv2.morphologyEx(grad_x, cv2.MORPH_CLOSE, rect_kernel)
    _, thresh = cv2.threshold(grad_x, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    thresh = cv2.erode(thresh, None, iterations=1)
    thresh = cv2.dilate(thresh, None, iterations=2)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    results = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if h <= 0 or w <= 0:
            continue
        aspect = w / float(h)
        area = w * h
        if not (2.0 <= aspect <= 6.2):
            continue
        if not (180 <= area <= 12000):
            continue
        if y < int(h_img * 0.25):
            continue

        roi_gray = gray[y : y + h, x : x + w]
        contrast = float(roi_gray.std())
        if contrast < 22.0:
            continue

        conf = min(0.92, 0.45 + (contrast / 120.0))
        px = int(round(w * pad_ratio))
        py = int(round(h * pad_ratio))
        nx = max(0, x - px)
        ny = max(0, y - py)
        nw = min(w_img - nx, w + 2 * px)
        nh = min(h_img - ny, h + 2 * py)
        results.append({
            "type": "plate",
            "x": nx,
            "y": ny,
            "w": nw,
            "h": nh,
            "conf": round(float(conf), 4),
        })
    return results


def _apply_redaction(img: np.ndarray, boxes: list[dict], ksize: int = 31) -> np.ndarray:
    """Apply deterministic Gaussian blur on detected PII regions."""
    out = img.copy()
    k = ksize if ksize % 2 == 1 else ksize + 1
    for b in boxes:
        x, y, w, h = int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
        roi = out[y : y + h, x : x + w]
        if roi.size == 0:
            continue
        blurred = cv2.GaussianBlur(roi, (k, k), 0)
        out[y : y + h, x : x + w] = blurred
    return out


# ---------------------------------------------------------------- 02 process
def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    if ctx.track != 't07':
        raise NotImplementedError(f'Pipeline is implemented for t07, not {ctx.track}')
    if ctx.track != 't07':
        raise NotImplementedError(f'Pipeline is implemented for t07, not {ctx.track}')
    images_df: pd.DataFrame = data["images"]
    face_pad = float(ctx.param("face_pad", 0.15))
    plate_pad = float(ctx.param("plate_pad", 0.12))
    blur_k = int(ctx.param("blur_kernel", 31))

    redacted_dir = ctx.output_dir / "redacted_images"
    redacted_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for row in images_df.itertuples(index=False):
        img_id = str(row.image_id)
        rel_path = str(getattr(row, "path", f"images/{img_id}.jpg"))
        img_path = ctx.input_dir / rel_path
        bgr = cv2.imread(str(img_path))
        if bgr is None:
            raise FileNotFoundError(f"Cannot read image: {img_path}")

        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        mean_luma = float(gray.mean())
        is_dark = mean_luma < 65.0

        faces = _detect_faces(gray, bgr, pad_ratio=face_pad)
        plates = _detect_plates(bgr, gray, pad_ratio=plate_pad)
        all_boxes = faces + plates

        redacted_bgr = _apply_redaction(bgr, all_boxes, ksize=blur_k)
        out_img_rel = f"redacted_images/{img_id}.jpg"
        cv2.imwrite(str(ctx.output_dir / out_img_rel), redacted_bgr)

        if not all_boxes:
            suspicion = 0.85 if is_dark else 0.35
            reason = "no_detection_dark_frame" if is_dark else "no_detection_check_small_pii"
            rows.append({
                "region_id": f"{img_id}:none",
                "image_id": img_id,
                "x": 0,
                "y": 0,
                "w": 0,
                "h": 0,
                "type": "none",
                "conf": 0.0,
                "score": round(suspicion, 6),
                "reason": reason,
                "suggested_action": "add_box_if_pii",
                "slice": f"dark={int(is_dark)}",
                "evidence_path": out_img_rel,
                "is_placeholder": 1,
            })
            continue

        for idx, box in enumerate(all_boxes):
            area = box["w"] * box["h"]
            uncertainty = 1.0 - box["conf"]
            small_bonus = 0.25 if area < 900 else 0.0
            dark_bonus = 0.20 if is_dark else 0.0
            suspicion = min(1.0, uncertainty + small_bonus + dark_bonus)

            reasons = []
            if box["conf"] < 0.7:
                reasons.append("low_conf")
            if area < 900:
                reasons.append("small_region")
            if is_dark:
                reasons.append("dark_condition")
            if not reasons:
                reasons.append("routine_check")

            rows.append({
                "region_id": f"{img_id}:{idx}",
                "image_id": img_id,
                "x": int(box["x"]),
                "y": int(box["y"]),
                "w": int(box["w"]),
                "h": int(box["h"]),
                "type": box["type"],
                "conf": box["conf"],
                "score": round(float(suspicion), 6),
                "reason": "+".join(reasons),
                "suggested_action": "verify_coverage",
                "slice": f"{box['type']}|dark={int(is_dark)}",
                "evidence_path": out_img_rel,
                "is_placeholder": 0,
            })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    budget = int(ctx.param("budget", 15))
    q = make_review_queue(
        scored,
        id_col="region_id",
        score_col="score",
        budget=budget,
        reason_col="reason",
        action_col="suggested_action",
        slice_col="slice",
        evidence_col="evidence_path",
    )
    write_review_queue(q, ctx.out("review_queue.csv"))
    render_contact_sheet(
        q,
        ctx.out("contact_sheet.html"),
        image_root=ctx.output_dir,
        title="T07 Face/Plate Redaction â€” Priority Review Queue",
    )
    return q


# ---------------------------------------------------------------- 04 review
def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    out = scored.copy()

    if reviewed_path.exists():
        reviewed = load_review_decisions(reviewed_path)
        ctx.stats["n_reviewed"] = len(reviewed)

        for r in reviewed.itertuples(index=False):
            dec = r.reviewer_decision
            rid = str(r.item_id)
            val = str(r.override_value).strip()

            if dec == "reject":
                mask = out["region_id"] == rid
                if mask.any():
                    before = out.loc[mask, ["x", "y", "w", "h"]].to_dict(orient="records")
                    out = out[~mask].copy()
                    ctx.audit.log(
                        actor=r.reviewer,
                        action="override:reject_box",
                        item_id=rid,
                        before=before,
                        after="removed",
                        reason=r.review_note,
                    )
            elif dec == "edit" and val:
                parts = [int(float(p.strip())) for p in val.split(",")]
                if len(parts) == 4:
                    nx, ny, nw, nh = parts
                    mask = out["region_id"] == rid
                    if mask.any():
                        before = out.loc[mask, ["x", "y", "w", "h"]].to_dict(orient="records")[0]
                        out.loc[mask, ["x", "y", "w", "h", "is_placeholder"]] = [nx, ny, nw, nh, 0]
                        ctx.audit.log(
                            actor=r.reviewer,
                            action="override:edit_box",
                            item_id=rid,
                            before=before,
                            after={"x": nx, "y": ny, "w": nw, "h": nh},
                            reason=r.review_note,
                        )
                    elif ":" in rid:
                        img_id = rid.split(":", 1)[0]
                        new_row = pd.DataFrame([{
                            "region_id": f"{img_id}:manual_{len(out)}",
                            "image_id": img_id,
                            "x": nx,
                            "y": ny,
                            "w": nw,
                            "h": nh,
                            "type": "manual",
                            "conf": 1.0,
                            "score": 0.0,
                            "reason": "human_added",
                            "suggested_action": "approved",
                            "slice": "manual",
                            "evidence_path": f"redacted_images/{img_id}.jpg",
                            "is_placeholder": 0,
                        }])
                        out = pd.concat([out, new_row], ignore_index=True)
                        ctx.audit.log(
                            actor=r.reviewer,
                            action="override:add_box",
                            item_id=rid,
                            before=None,
                            after={"x": nx, "y": ny, "w": nw, "h": nh},
                            reason=r.review_note,
                        )
            elif dec == "approve":
                ctx.audit.log(
                    actor=r.reviewer,
                    action="review:approve",
                    item_id=rid,
                    reason=r.review_note,
                )

    final_boxes = out[(out["is_placeholder"] == 0) & (out["w"] > 0) & (out["h"] > 0)].copy()
    return final_boxes


# ---------------------------------------------------------------- 05 decide
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    owner = ctx.config.get("owner", "TODO-owner")
    n_boxes = len(final)
    n_reviewed = int(ctx.stats.get("n_reviewed", 0))

    if n_boxes == 0:
        return Decision(
            status="HOLD",
            reason_code="ZERO_REDACTIONS_DETECTED",
            reason="No PII regions detected across dataset; high risk of PII leakage.",
            owner=owner,
            scope=ctx.track,
        )

    if n_reviewed == 0:
        return Decision(
            status="HOLD",
            reason_code="HUMAN_REVIEW_PENDING",
            reason="Auto-redaction completed, awaiting human audit on top-15 uncertain regions.",
            owner=owner,
            scope=ctx.track,
            evidence=[str(ctx.out("redactions.csv")), str(ctx.out("contact_sheet.html"))],
        )

    return Decision(
        status="GO",
        reason_code="REDACTED_AND_AUDITED",
        reason=f"Exported {n_boxes} redacted regions after {n_reviewed} human review decisions.",
        owner=owner,
        scope=ctx.track,
        evidence=[str(ctx.out("redactions.csv")), str(ctx.out("audit_log.jsonl"))],
    )


def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    data = load(ctx)
    scored = process(ctx, data)
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)
    cols = spec.pred_columns + [c for c in final.columns if c not in spec.pred_columns and c != "is_placeholder"]
    final[cols].to_csv(ctx.out(spec.pred_file), index=False)
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))
