import os
import datetime

from .progress_reporter import ProgressReporter


def generate_output_name(base_name: str, suffix: str, ext: str) -> str:
    """生成带时间戳的输出文件名，避免新旧内容覆盖。
    
    Args:
        base_name: 基础名称（如 "mixed_output"、"video_name_audio"）
        suffix: 额外后缀（如 "_extracted"、"_mixed"、""，会被放在时间戳前面）
        ext:   文件扩展名（如 "wav"、"mp4"，不带点）
    
    Returns:
        完整文件名，格式为 "{base_name}{suffix}_{YYYYMMDD_HHMMSS}.{ext}"
        例如: "mixed_output_20260804_154530.wav"
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base_name}{suffix}_{timestamp}.{ext}"


def resolve_output_path(output_dir: str, base_name: str, suffix: str = "", ext: str = "wav") -> str:
    """生成带时间戳的完整输出路径，并确保输出目录存在。
    
    Args:
        output_dir: 输出目录路径
        base_name: 基础名称
        suffix:   额外后缀
        ext:      文件扩展名（不带点）
    
    Returns:
        完整输出路径
    """
    os.makedirs(output_dir, exist_ok=True)
    filename = generate_output_name(base_name, suffix, ext)
    return os.path.join(output_dir, filename)


__all__ = ["ProgressReporter", "generate_output_name", "resolve_output_path"]
