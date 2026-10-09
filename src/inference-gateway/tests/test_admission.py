"""Boundary checks for admission payload measurements."""

from base64 import b64encode

import pytest
from app.admission import largest_image_bytes


@pytest.mark.parametrize("byte_count", [1, 2, 3, 1048576])
def test_data_url_padding_does_not_count_as_image_bytes(byte_count):
    encoded = b64encode(b"x" * byte_count).decode("ascii")
    messages = [{"content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}]}]
    assert largest_image_bytes(messages) == byte_count
