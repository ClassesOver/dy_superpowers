"""调研答卷读取只接受模块、范围与问题标识，拒绝旧检查键参数。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
import server


@pytest.mark.parametrize('extra', [dict(checks=[]),
    dict(checks=[dict(check_order='1', research_id='01')]*2),
    dict(check_order='1', research_id='01', checks=[dict(check_order='1', research_id='01')])])
def test_read_rejects_invalid_or_conflicting_batch_keys(tmp_path, extra):
    result, error = server.execute_tool('dy_module_memory_read',
        dict(trace_id='invalid-batch', module='expense', **extra), tmp_path)
    assert result is None and error['code'] == -32602
