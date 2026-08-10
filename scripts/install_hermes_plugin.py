"""同步仓库版 remembrance-hook 插件到 Hermes home（备份旧版，不删除）。

用法：
  python scripts/install_hermes_plugin.py [--hermes-home <path>]
  默认 Hermes home = C:/Users/Asus/AppData/Local/hermes

行为：
  1. 备份现有插件目录到 plugins-backup/remembrance-hook-YYYYMMDD（插件扫描目录之外），
     且备份内 plugin.yaml 改名为 plugin.yaml.disabled —— 防止被 Hermes 插件加载器当作
     同名候选扫描到（v1.0.0 曾因备份留在 plugins/ 内且同名，旧版覆盖新版被加载）
  2. 复制 hermes-plugin/remembrance-hook/（__init__.py + plugin.yaml）到目标
  3. 自检：plugins/ 下声明同名插件的候选唯一且为目标目录
  4. 提示重启 Hermes 生效
"""
import argparse
import datetime
import re
import shutil
import sys
from pathlib import Path

DEFAULT_HERMES_HOME = Path(r"C:/Users/Asus/AppData/Local/hermes")
PLUGIN_SRC = Path(__file__).resolve().parent.parent / "hermes-plugin" / "remembrance-hook"
PLUGIN_NAME = "remembrance-hook"


def _manifest_name(plugin_dir: Path) -> str | None:
    """读目录内 plugin.yaml/plugin.yml 的 name 字段（正则，保持脚本零第三方依赖）。"""
    for fn in ("plugin.yaml", "plugin.yml"):
        p = plugin_dir / fn
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"(?m)^name\s*:\s*(\S+)", text)
            return m.group(1).strip().strip("'\"") if m else None
    return None


def backup_existing(target: Path, backups_dir: Path) -> Path | None:
    """把现有插件目录备份到扫描目录之外，并将备份内 manifest 失效化。"""
    if not target.is_dir():
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    backups_dir.mkdir(parents=True, exist_ok=True)
    backup = backups_dir / f"{PLUGIN_NAME}-{stamp}"
    if backup.exists():
        shutil.rmtree(backup)  # 仅清理同名旧备份（备份目录本身）
    shutil.copytree(target, backup)
    for fn in ("plugin.yaml", "plugin.yml"):
        p = backup / fn
        if p.is_file():
            p.rename(p.with_name(fn + ".disabled"))
    print(f"[OK] 旧插件已备份到 {backup}（manifest 已失效化）")
    shutil.rmtree(target)
    return backup


def deploy(plugin_src: Path, plugins_dir: Path, backups_dir: Path) -> Path | None:
    """部署插件并返回备份路径（无旧版时为 None）。"""
    target = plugins_dir / PLUGIN_NAME
    backup = backup_existing(target, backups_dir)
    shutil.copytree(plugin_src, target)
    print(f"[OK] 已部署到 {target}")
    print("[OK] 文件: " + ", ".join(p.name for p in plugin_src.iterdir() if p.is_file()))
    return backup


def validate_no_duplicate(plugins_dir: Path) -> bool:
    """自检：plugins/ 下声明同名插件的候选必须唯一且为目标目录。"""
    candidates = [
        child for child in sorted(plugins_dir.iterdir())
        if child.is_dir() and _manifest_name(child) == PLUGIN_NAME
    ]
    if len(candidates) != 1 or candidates[0].name != PLUGIN_NAME:
        print(
            f"[ERR] 插件发现冲突：声明 name={PLUGIN_NAME} 的目录有 {len(candidates)} 个: "
            + ", ".join(str(c) for c in candidates),
            file=sys.stderr,
        )
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", type=Path, default=DEFAULT_HERMES_HOME)
    args = parser.parse_args()

    if not PLUGIN_SRC.is_dir():
        print(f"[ERR] 源插件目录不存在: {PLUGIN_SRC}", file=sys.stderr)
        return 1

    hermes_home = args.hermes_home
    plugins_dir = hermes_home / "plugins"
    if not plugins_dir.is_dir():
        print(f"[ERR] Hermes plugins 目录不存在: {plugins_dir}（确认 Hermes home 路径）",
              file=sys.stderr)
        return 1

    deploy(PLUGIN_SRC, plugins_dir, hermes_home / "plugins-backup")

    if not validate_no_duplicate(plugins_dir):
        return 1

    print("")
    print("下一步：重启 Hermes 桌面版使插件重新加载；验证：")
    print("  1. Hermes 日志出现 'on_session_end 对话写入已注册'")
    print("  2. 一轮对话结束后，记忆库出现候选（GET /candidates/pending）")
    print("回滚：删除 plugins/remembrance-hook，把 plugins-backup/remembrance-hook-YYYYMMDD")
    print("      移回 plugins/remembrance-hook，并把其 plugin.yaml.disabled 改回 plugin.yaml 后重启")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())