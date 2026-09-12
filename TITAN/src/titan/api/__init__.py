"""Optional FastAPI control plane.

Import :func:`titan.api.app.create_app` (or the module-level ``app``) only when
the ``api`` optional dependencies are installed. Kept out of this package's
eager imports so the voice agent never requires FastAPI to run.
"""
