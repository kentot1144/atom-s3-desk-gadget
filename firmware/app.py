"""Entry point for main.py.

The real code lives in the `desk` package.  It is split into small modules
because MicroPython has to compile a file before running it, and 2.3.1 only
has ~112 KB of heap at boot - a single 64 KB app.py cannot be compiled there.
"""
from desk.app import run

__all__ = ("run",)
