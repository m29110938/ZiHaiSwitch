"""把專案的功能與使用到的技術同步成一篇 Obsidian 筆記。

    .venv/bin/python tools/obsidian_sync.py --dir "<vault>/筆記"   # 第一次：設定資料夾並同步
    .venv/bin/python tools/obsidian_sync.py                         # 之後：直接同步

資料夾設定存在 ~/.zihaiswitch/obsidian.json（不進 git）。`tools/release.py build` 完成後會自動同步。
筆記內容由 README、docs/TECH.md、各模組說明、requirements.txt、CHANGELOG 組成，每次整篇覆蓋。
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import version as V  # noqa: E402

CONFIG = Path.home() / ".zihaiswitch" / "obsidian.json"
NOTE_NAME = "自嗨軸 ZiHaiSwitch.md"


def configured_dir() -> Path | None:
    try:
        d = Path(json.loads(CONFIG.read_text("utf-8"))["dir"]).expanduser()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return d if d.is_dir() else None


def save_dir(d: Path) -> None:
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"dir": str(d)}, ensure_ascii=False), "utf-8")


# ---------------------------------------------------------------- 內容
def _read(rel: str) -> str:
    try:
        return (ROOT / rel).read_text("utf-8")
    except OSError:
        return ""


def _demote(md: str, levels: int = 1) -> str:
    """標題降級，放進筆記的小節裡。"""
    return re.sub(r"^(#+) ", lambda m: "#" * (len(m.group(1)) + levels) + " ", md, flags=re.M)


def readme_parts() -> tuple[str, str]:
    """回傳（簡介、功能清單）：README 標題後到第一個 ## 之前。"""
    text = _read("README.md")
    head = text.split("\n## ", 1)[0].split("\n", 1)[-1].strip()
    intro = "\n".join(l for l in head.splitlines() if l and not l.startswith("- ")).strip()
    features = "\n".join(l for l in head.splitlines() if l.startswith("- "))
    return intro, features


def tech_section() -> str:
    body = _read("docs/TECH.md")
    body = re.sub(r"^# .*\n+", "", body, count=1)  # 去掉文件標題，改用筆記的小節標題
    return _demote(body).strip()


def module_table() -> str:
    rows = ["| 檔案 | 說明 |", "|---|---|"]
    files = sorted(ROOT.glob("*.py")) + sorted((ROOT / "tools").glob("*.py"))
    for f in files:
        try:
            doc = ast.get_docstring(ast.parse(f.read_text("utf-8"))) or ""
        except (OSError, SyntaxError):
            doc = ""
        first = doc.strip().splitlines()[0] if doc.strip() else ""
        rows.append(f"| `{f.relative_to(ROOT)}` | {first.replace('|', '／')} |")
    return "\n".join(rows)


def requirements() -> str:
    lines = [l.strip() for l in _read("requirements.txt").splitlines()
             if l.strip() and not l.startswith("#")]
    return "\n".join(f"- `{l}`" for l in lines)


def _tag_date(ver: str) -> str:
    r = subprocess.run(["git", "log", "-1", "--format=%cs", f"v{ver}"], cwd=ROOT,
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def changelog(repo_url: str) -> str:
    text = _read("CHANGELOG.md")
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    out = []
    for p in parts:
        title, _, body = p.partition("\n")
        m = re.match(r"v(\d+\.\d+\.\d+)", title.strip())
        if m:
            date = _tag_date(m.group(1))
            if not date and m.group(1) == V.VERSION:
                date = dt.date.today().isoformat()  # 剛打包、還沒建 tag 的版本
            link = f"[{title.strip()}]({repo_url}/releases/tag/{title.strip()})"
            title = link + (f"（{date}）" if date else "")
        out.append(f"### {title.strip()}\n\n{body.strip()}")
    return "\n\n".join(out)


def render() -> str:
    repo = f"https://github.com/{V.UPDATE_REPO}"
    intro, features = readme_parts()
    today = dt.date.today().isoformat()
    return f"""---
tags:
  - 專案
  - macOS
  - Python
版本: {V.VERSION}
更新: {today}
GitHub: {repo}
---

# {V.APP_NAME}

{intro}

## 功能

{features}

## 使用到的技術

{tech_section()}

## 程式結構

{module_table()}

## 執行時相依套件

{requirements()}

## 版本紀錄

{changelog(repo)}

## 連結

- GitHub：{repo}
- 下載：{repo}/releases/latest

%% 由 ZiHaiSwitch 專案的 tools/obsidian_sync.py 自動產生，發版時會整篇覆蓋 %%
"""


def sync(target: Path | None = None) -> Path | None:
    d = target or configured_dir()
    if d is None:
        return None
    note = d / NOTE_NAME
    tmp = note.with_suffix(".tmp")
    tmp.write_text(render(), "utf-8")
    tmp.replace(note)
    return note


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="Obsidian vault 內要放筆記的資料夾（會記住）")
    a = ap.parse_args()
    if a.dir:
        d = Path(a.dir).expanduser()
        if not d.is_dir():
            sys.exit(f"資料夾不存在：{d}")
        save_dir(d.resolve())
    out = sync()
    if out is None:
        sys.exit("尚未設定 Obsidian 資料夾，請加上 --dir")
    print(f"已同步：{out}")
