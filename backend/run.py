"""Launcher so the API can be started directly (IDE Run button or `python run.py`).

Running app/main.py directly fails because its relative imports need the package
context. Starting uvicorn with the import string "app.main:app" provides it.
"""
from __future__ import annotations

import os
import sys

import uvicorn

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
