"""Free Tier Tracker: watches cloud providers' free-tier pages and records what changes.

The modules split along one line. `providers`, `text` and `quotas` are pure Python with
no Workers imports, so the test suite runs them under plain pytest. `store` and the
handlers in `entry.py` talk to bindings and the network.
"""
