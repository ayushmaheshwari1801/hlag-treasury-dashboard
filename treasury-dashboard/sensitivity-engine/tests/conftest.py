from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from treasury_sensitivity.audit import AuditStore
from treasury_sensitivity.service import SensitivityService

AS_OF = date(2026, 10, 31)


@pytest.fixture
def service():
    tz = ZoneInfo("Asia/Singapore")
    return SensitivityService(audit=AuditStore(":memory:"), clock=lambda: datetime(2026, 10, 31, 9, 0, tzinfo=tz))
