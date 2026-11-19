"""
api_v1.py
---------
Backward-compatibility facade for Saptic AI Mobile & Web Companion API.
The canonical production implementation now lives in:
    `api/v1/endpoints.py`

This facade re-exports all endpoints, models, caches, and methods,
and binds sys.modules['api_v1'] to api.v1.endpoints for seamless test mocking.
"""

import sys
import api.v1.endpoints as _v1_endpoints_module

# Alias in sys.modules so patch("api_v1.<func>") modifies api.v1.endpoints directly
sys.modules["api_v1"] = _v1_endpoints_module

# Re-export all globals for static analysis and explicit namespace consumers
for _key, _val in _v1_endpoints_module.__dict__.items():
    if not _key.startswith("__"):
        globals()[_key] = _val

router = _v1_endpoints_module.router
