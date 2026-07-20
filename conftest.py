"""Root pytest configuration.

Its sole purpose is to sit at the repository root so pytest adds this
directory to ``sys.path`` (via rootdir insertion), allowing test modules to
``import app`` regardless of where ``pytest`` is invoked from.
"""
