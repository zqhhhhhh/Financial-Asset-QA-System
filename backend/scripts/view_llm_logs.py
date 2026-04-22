"""
实时查看 LLM 调用日志（prompt + response）。

用法：
    python scripts/view_llm_logs.py           # 显示最近 20 条调用
    python scripts/view_llm_logs.py -n 5      # 显示最近 5 条调用
    python scripts/view_llm_logs.py --follow  # 实时追踪新日志（类似 tail -f）
    python scripts/view_llm_logs.py --clear   # 清空日志文件
"""

import argparse
import re
import sys
import time
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parents[1].parent / "logs" / "llm.log"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="View LLM call logs")
    p.add_argument("-n", type=int, default=20, help="显示最近 N 条调用（默认20）")
    p.add_argument("--follow", "-f", action="store_true", help="实时追踪新日志")
    p.add_argument("--clear", action="store_true", help="清空日志文件")
    return p.parse_args()


def split_calls(text: str) -> list[str]:
    """按 ═══ 边界分割每次 LLM 调用块"""
    blocks = re.split(r"(?=\n?={50,})", text)
    return [b.strip() for b in blocks if b.strip() and "═" in b]


def display_calls(calls: list[str], n: int) -> None:
    recent = calls[-n:] if len(calls) > n else calls
    for i, block in enumerate(recent, 1):
        print(f"\n{'─'*72}")
        print(block)
    print(f"\n{'─'*72}")
    print(f"[共显示 {len(recent)} / {len(calls)} 条调用记录]")


def follow_mode(log_path: Path) -> None:
    print(f"[实时追踪] {log_path}  (Ctrl+C 退出)\n")
    with log_path.open("r", encoding="utf-8") as f:
        f.seek(0, 2)  # 跳到文件末尾
        while True:
            line = f.readline()
            if line:
                print(line, end="")
            else:
                time.sleep(0.3)


def main() -> None:
    args = parse_args()

    if not LOG_FILE.exists():
        print(f"日志文件不存在：{LOG_FILE}")
        print("请先启动后端服务并发送一条消息后再查看。")
        sys.exit(1)

    if args.clear:
        LOG_FILE.write_text("", encoding="utf-8")
        print("日志已清空。")
        return

    if args.follow:
        follow_mode(LOG_FILE)
        return

    content = LOG_FILE.read_text(encoding="utf-8")
    calls = split_calls(content)
    if not calls:
        print("日志文件为空或格式不符。")
        return
    display_calls(calls, args.n)


if __name__ == "__main__":
    main()
