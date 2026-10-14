# Vendor plugin tests (fixtures)

These suites exercise vendor parsers and `tools/capability_report.py`
mapping against `tests/fixtures/<vendor>/`. They are **not** hardware
certification and cannot award Level ≥ 1.

Run:

```text
python3 -m unittest discover -s tests/vendor -p 'test_*.py'
```

Do not add tests that call live `devlink`/`sriov_numvfs` against the
developer machine. Inject `samples=` or a fake runner.
