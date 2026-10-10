"""Run current functional/data/permission regressions; never launch experiments."""
import sys
import unittest
from pathlib import Path
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
loader = unittest.TestLoader()
suite = unittest.TestSuite()
for pattern in ('test_date_product.py', 'test_calendar_session_foundation.py', 'test_calendar_config.py', 'test_agent_refactor.py', 'test_function_calling.py', 'test_memory_overview.py'):
    suite.addTests(loader.discover(str(root / 'tests'), pattern=pattern))
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
