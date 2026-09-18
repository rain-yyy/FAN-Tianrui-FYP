from pathlib import Path
from typing import Optional


def resolve_path_under_root(root: Path, input_path: str) -> Optional[Path]:
    """
    将 input_path 解析为绝对路径，并确保其位于 root 之内。
    用于防止通过 `../` 或绝对路径逃逸出预期目录（路径穿越）。
    返回 None 表示 input_path 逃逸出了 root。
    """
    root_resolved = root.expanduser().resolve()
    raw = Path(input_path).expanduser()

    if raw.is_absolute():
        target = raw.resolve()
    else:
        target = (root_resolved / raw).resolve()

    try:
        target.relative_to(root_resolved)
    except ValueError:
        return None
    return target
