from __future__ import annotations

import fitz

from backend.services.visual_asset_linker import caption_match_score


def test_caption_match_prefers_nearby_over_distant_caption():
    image = fitz.Rect(50, 50, 250, 200)
    nearby = fitz.Rect(55, 205, 245, 235)
    distant = fitz.Rect(300, 500, 500, 540)

    near_distance, near_confidence = caption_match_score(image, nearby)
    far_distance, far_confidence = caption_match_score(image, distant)

    assert near_distance < far_distance
    assert near_confidence > far_confidence
