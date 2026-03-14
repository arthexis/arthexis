"""Legacy role bootstrap smoke tests were removed.

These checks asserted behavior around role-specific environment bootstrapping
that is no longer required for node configuration.
"""

import pytest


pytestmark = pytest.mark.skip(
    reason="Role bootstrap environment enforcement is no longer required."
)
