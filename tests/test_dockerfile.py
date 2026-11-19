"""The image's base layers are pinned by digest, so a rebuild cannot pick up a moved tag."""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"


def test_every_base_image_is_pinned_by_digest() -> None:
    text = DOCKERFILE.read_text()
    images = re.findall(r"^ARG \w+_IMAGE=(\S+)$", text, flags=re.MULTILINE)
    froms = re.findall(r"^FROM (\S+)", text, flags=re.MULTILINE)

    assert images, "the base images are named in ARG ..._IMAGE lines"
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in images)
    assert all(image.startswith("${") for image in froms), "every FROM uses a pinned ARG, never a bare tag"
    assert "COPY --from=" not in text.replace("COPY --from=uv ", "").replace("COPY --from=build ", "")
