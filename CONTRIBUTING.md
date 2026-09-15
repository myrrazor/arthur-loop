# Contributing

Arthur Loop is a Python CLI with a small native macOS companion. Keep changes focused, add a test for behavior, and open a pull request against `main`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

ArthurBar changes also run its Swift suite:

```bash
cd menubar/ArthurBar
swift test
```

Before handing work off, make sure both suites pass and the README still matches the actual CLI — `tests/test_quickstart.py` executes the README quickstart literally and fails if the two drift. Pull requests target `main`.
