"""同步仓库版 remembrance-hook 插件到 Hermes home（备份旧版，不删除）。

用法：
  python scripts/install_hermes_plugin.py [--hermes-home <path>]
  默认 Hermes home = C:/Users/Asus/AppData/Local/hermes

行为：
  1. 备份现有插件目录为 plugins/remembrance-hook.bak-YYYYMMDD（若存在）
  2. 复制 hermes-plugin/remembrance-hook/（__init__.py + plugin.yaml）到目标
  3. 提示重启 Hermes 生效
"""
import argparse
import datetime
import shutil
import sys
from pathlib import Path

DEFAULT_HERMES_HOME = Path(r"C:/Users/Asus/AppData/Local/hermes")
PLUGIN_SRC = Path(__file__).resolve().parent.parent / "hermes-plugin" / "remembrance-hook"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", type=Path, default=DEFAULT_HERMES_HOME)
    args = parser.parse_args()

    if not PLUGIN_SRC.is_dir():
        print(f"[ERR] 源插件目录不存在: {PLUGIN_SRC}", file=sys.stderr)
        return 1

    plugins_dir = args.hermes_home / "plugins"
    if not plugins_dir.is_dir():
        print(f"[ERR] Hermes plugins 目录不存在: {plugins_dir}（确认 Hermes home 路径）",
              file=sys.stderr)
        return 1

    target = plugins_dir / "remembrance-hook"
    # 备份旧版（改名，不删除）
    if target.is_dir():
        stamp = datetime.datetime.now().strftime("%Y%m%d")
        backup = plugins_dir / f"remembrance-hook.bak-{stamp}"
        if backup.exists():
            shutil.rmtree(backup)  # 仅清理同名旧备份（备份目录本身）
        shutil.copytree(target, backup)
        print(f"[OK] 旧插件已备份到 {backup}")
        shutil.rmtree(target)

    shutil.copytree(PLUGIN_SRC, target)
    print(f"[OK] 已部署到 {target}")
    print("[OK] 文件: " + ", ".join(p.name for p in PLUGIN_SRC.iterdir() if p.is_file()))
    print("")
    print("下一步：重启 Hermes 桌面版使插件重新加载；验证：")
    print("  1. Hermes 日志出现 'on_session_end 对话写入已注册'")
    print("  2. 一轮对话结束后，记忆库出现候选（GET /candidates/pending）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
