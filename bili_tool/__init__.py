"""bili-tool: turn a bilibili URL into a timeline-aligned transcript + visual-notes bundle."""

__version__ = "0.1.0"

from .probe import probe  # noqa: E402  (after __version__: config.py imports it back from here)
from .schema import ProbeResult  # noqa: E402

__all__ = ["__version__", "probe", "ProbeResult"]
