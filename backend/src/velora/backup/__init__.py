"""Backups and restore (P11, AD-21, AD-36).

- `crypto`: dumps are encrypted to a public key; the secret key stays offline.
- `storage`: a directory, or any S3-compatible bucket (free tiers, AD-35).
- `job`: one backup: roles, database and a manifest, from one snapshot.
- `restore`: restore into a new database, then verify it (counts, RLS, grants, isolation).
- `python -m velora.backup` is the command line.
"""
