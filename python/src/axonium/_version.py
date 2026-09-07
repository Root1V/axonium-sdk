"""The single source of truth for this package's version.

The build backend reads it from here and ``axonium.__version__`` re-exports it, so the packaged
version and the attribute callers see can never disagree. The legacy SDK kept the two in separate
files and they drifted by three releases.
"""

__version__ = "1.0.0rc1"
