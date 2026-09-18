from enum import Enum
from typing import Any


def to_jsonable(value: Any) -> Any:
    """
    将 numpy/自定义对象中的标量递归转换为 Python 原生可 JSON 序列化类型。
    """
    try:
        import numpy as np  # 延迟导入，避免无依赖环境报错
        numpy_scalar_types = (np.generic,)
    except Exception:
        numpy_scalar_types = tuple()

    if isinstance(value, numpy_scalar_types):
        return value.item()
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    return value
