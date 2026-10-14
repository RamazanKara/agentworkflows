"""Make the gateway's tests runnable from any working directory, and keep them fast.

The tests import the service as ``app`` and their shared helpers as ``tests.*``, which
resolve only when this service directory is on ``sys.path``. Running pytest from the
service directory gets that for free from rootdir detection; running it from the repo
root (the natural thing to type, and what CI matrix steps and editors tend to do) used
to die at collection instead. pytest imports this conftest before collecting anything
beneath it, so the path is set either way.
"""

import sys
from pathlib import Path
from typing import Any

_SERVICE_DIR = str(Path(__file__).resolve().parent)
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)


def _share_fastapi_type_adapters() -> None:
    """Build each distinct FastAPI parameter/response type adapter once per test session.

    Nearly every test builds its own app, and building one constructs a pydantic
    ``TypeAdapter`` for every route parameter and response model: about two thirds of
    ``create_app`` and, before this, most of the suite's run time. The adapters are
    immutable once built and depend only on the annotation, its metadata, the field
    attributes and the config, so equal fields can share one. Anything that cannot be
    keyed safely, or a FastAPI release that moves these internals, falls back to
    FastAPI's own construction. The service itself builds one app per process and is
    unaffected.
    """
    try:
        from fastapi._compat import v2
    except ImportError:  # pragma: no cover - a FastAPI release without these internals
        return
    model_field = getattr(v2, "ModelField", None)
    field_dict = getattr(v2, "asdict", None)
    if model_field is None or field_dict is None:  # pragma: no cover
        return
    build = model_field.__post_init__
    adapters: dict[Any, Any] = {}

    def post_init(self: Any) -> None:
        try:
            fields = field_dict(self.field_info)
            key = (
                fields["annotation"],
                tuple(fields["metadata"]),
                repr(sorted(fields["attributes"].items())),
                repr(self.config),
            )
            adapter = adapters.get(key)
        except Exception:  # unhashable or unorderable parts: build as FastAPI does
            build(self)
            return
        if adapter is None:
            build(self)
            adapters[key] = self._type_adapter
        else:
            self._type_adapter = adapter

    model_field.__post_init__ = post_init


_share_fastapi_type_adapters()
