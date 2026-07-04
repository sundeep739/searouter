"""Loads secrets from .env (gitignored) into the process environment.

Copernicus Marine's toolbox reads COPERNICUSMARINE_SERVICE_USERNAME/_PASSWORD
straight from the environment itself — this module just makes sure .env is
loaded before anything checks for them, so no credentials ever need to be
written into source files or into copernicusmarine's own login cache."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
