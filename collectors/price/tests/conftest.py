# -*- coding: utf-8 -*-
"""pytest: test_basket_symbol.py is a SCRIPT (module-level body, ends in sys.exit), which CI
runs as `python tests/test_basket_symbol.py`. Importing it at collection time raised
SystemExit and crashed bare `pytest -q` with INTERNALERROR (ILU Ch8g). It is skipped here and
covered by test_basket_symbol_script.py, which runs it as a subprocess and asserts exit 0."""
collect_ignore = ["test_basket_symbol.py"]
