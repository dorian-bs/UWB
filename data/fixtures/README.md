# Fixtures

Small, committed sample data used so the host can be exercised without hardware.

- `sample_session.ndjson` — 120 synthetic range samples at 5 Hz around a ~2 m distance,
  with a simulated NLOS obstruction (positive bias, larger spread) over samples 50–90.
  Used by `make replay`.

Real recordings go to `data/sessions/` and are intentionally untracked.
