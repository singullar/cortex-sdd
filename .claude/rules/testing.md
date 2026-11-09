# Testing

- Run `python3 -m unittest discover -s test` before opening a pull request.
- Every script has a `test/test_<script>.py`. Test against real temp directories and real git repositories rather than mocks.
- `test/test_plugin_integrity.py` checks that references resolve and that removed concepts stay removed. Fix the cause; do not add exceptions to it.
