"""Shared backend helpers — every module here is pure infrastructure that has
no domain logic. Routes / DB / cache code imports from these; nothing here
imports from a route module (no circular risk).
"""
