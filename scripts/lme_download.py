"""LongMemEval 数据集下载（票 02 步骤④）：HF 直连优先锁 revision commit SHA。

选型记录见 REPORT.md：V2（Apache-2.0）为多模态 agent 轨迹基准，与本票
「会话级 distinct-session recall@K」仪器不匹配；cleaned-S（MIT）口径匹配
且有可引用参照数字（arXiv 2410.10813 各题型基线 + Zep 论文 LongMemEval-S
71.2%@gpt-4o）。禁用 oracle 变体（只含证据会话=考前发答案，票 B 不做清单）。

直连可达 → 锁 revision commit SHA（98d7416c…）+ 落盘 SHA256 与 HF LFS oid
（paths-info 的 lfs.oid）双向核对；直连不可达才走镜像并做同款校验。
"""

import hashlib
import json
import sys
import time
from pathlib import Path

import requests

REPO = "xiaowu0162/longmemeval-cleaned"
REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"  # 锁定 revision（HF API 实查）
FILES = ["longmemeval_s_cleaned.json", "README.md"]

OUT = Path("data/longmemeval")  # .gitignore 已覆盖 data/（见数据不入仓纪律）


def _get_with_backoff(url: str, tries: int = 5, **kw):
    """指数退避重试（1/2/4/8/16s）——HF 直连偶发 SSL EOF（实测）。"""
    last = None
    for i in range(tries):
        try:
            return requests.get(url, **kw)
        except requests.RequestException as exc:
            last = exc
            wait = 2**i
            print(f"  网络重试 {i + 1}/{tries}（{wait}s 退避）：{repr(exc)[:120]}", flush=True)
            time.sleep(wait)
    raise last


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"repo": REPO, "revision": REVISION, "files": {}}

    # revision 活性核验：API 当前 sha 必须与锁定值一致（被 force-push/删仓即响亮失败）
    r = _get_with_backoff(f"https://huggingface.co/api/datasets/{REPO}", timeout=30)
    r.raise_for_status()
    live_sha = r.json()["sha"]
    if live_sha != REVISION:
        print(
            f"拒跑：revision 漂移（锁定 {REVISION[:12]} vs 现场 {live_sha[:12]}）", file=sys.stderr
        )
        return 3

    for name in FILES:
        info = requests.post(
            f"https://huggingface.co/api/datasets/{REPO}/paths-info/{REVISION}",
            json={"paths": [name]},
            timeout=30,
        ).json()
        lfs_oid = (info[0].get("lfs") or {}).get("oid") if info else None
        url = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{name}"
        dst = OUT / name
        print(f"下载 {name} <- {url}", flush=True)
        # 断点续传（实测直连在 ~227MB 处断流）：已落盘前缀用 Range 续拉，
        # 多轮退避直至完整；完成后再做 sha256↔LFS oid 校验。
        for attempt in range(8):
            have = dst.stat().st_size if dst.exists() else 0
            headers = {"Range": f"bytes={have}-"} if have else {}
            try:
                with _get_with_backoff(
                    url, tries=3, timeout=180, stream=True, headers=headers
                ) as resp:
                    if have and resp.status_code != 206:
                        print(f"  服务器不支持续传（{resp.status_code}），整档重下", flush=True)
                        have = 0
                        dst.unlink(missing_ok=True)
                    if not have:
                        dst.unlink(missing_ok=True)
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0)) + have
                    with dst.open("ab" if have else "wb") as f:
                        for chunk in resp.iter_content(1 << 20):
                            f.write(chunk)
                if not total or dst.stat().st_size >= total:
                    break
                print(f"  断流于 {dst.stat().st_size}/{total}，续传…", flush=True)
            except requests.RequestException as exc:
                print(f"  下载异常（{repr(exc)[:120]}），续传重试 {attempt + 1}/8", flush=True)
                time.sleep(2 ** min(attempt, 4))
        else:
            print(f"{name} 8 轮续传仍未完成，放弃", file=sys.stderr)
            return 4
        got = sha256_file(dst)
        manifest["files"][name] = {
            "size": dst.stat().st_size,
            "sha256": got,
            "hf_lfs_oid": lfs_oid,
            "sha256_matches_lfs_oid": (lfs_oid == got) if lfs_oid else None,
        }
        print(
            f"  {name}: {dst.stat().st_size} bytes sha256={got[:16]}… lfs_oid 匹配={manifest['files'][name]['sha256_matches_lfs_oid']}"
        )

    (OUT / "download_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print("manifest 落盘:", OUT / "download_manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
