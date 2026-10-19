#!/usr/bin/env python3
"""publish_due.py — 雲端（GitHub Actions）版 IG 發布器。

在 GitHub Actions runner 上執行，不需要任何本機電腦開機。
讀取 posts/<slug>/post.json，把「已到排定時間且尚未發布」的貼文發成 IG Reels。

必要的環境變數：
  IG_USER_ID          IG 專業帳號 ID
  IG_ACCESS_TOKEN     長效存取權杖（放 GitHub Secrets）
選用：
  IG_GRAPH_VERSION    預設 v26.0
  VIDEO_BASE_URL      影片公開網址前綴；未設定時自動依序嘗試 jsDelivr → raw.githubusercontent
  DRY_RUN=1           只列出，不發布
  FORCE=1             忽略排定時間
  MAX_POSTS           本次最多發幾篇（預設 2）
  DAILY_CAP           每日發布上限（預設 6）
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "posts"
HOST = os.environ.get("IG_GRAPH_HOST", "https://graph.instagram.com").rstrip("/")
VERSION = os.environ.get("IG_GRAPH_VERSION", "v26.0").strip("/")
BASE = f"{HOST}/{VERSION}"
IG_USER_ID = os.environ.get("IG_USER_ID", "").strip()
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")
MAX_POSTS = int(os.environ.get("MAX_POSTS") or 2)
DAILY_CAP = int(os.environ.get("DAILY_CAP") or 6)
REPO = os.environ.get("GITHUB_REPOSITORY", "")          # owner/repo
SHA = os.environ.get("GITHUB_SHA", "")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")


class ApiError(RuntimeError):
    def __init__(self, message, code=None, subcode=None, status=None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.status = status


def api(method: str, path: str, params: dict | None = None) -> dict:
    params = {k: v for k, v in (params or {}).items() if v is not None}
    params["access_token"] = TOKEN
    url = f"{BASE}{path}"
    if method == "GET":
        request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", method="GET")
    else:
        request = urllib.request.Request(
            url, data=urllib.parse.urlencode(params).encode(),
            method=method, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        try:
            err = json.loads(body).get("error", {})
        except json.JSONDecodeError:
            err = {}
        raise ApiError(err.get("message") or body[:300], err.get("code"),
                       err.get("error_subcode"), error.code) from None
    except urllib.error.URLError as error:
        raise ApiError(f"network error: {error.reason}") from None


def head_probe(url: str) -> tuple[bool, str]:
    """回傳 (是否可讀, Content-Type)。IG 伺服器抓影片時對 content-type 敏感。"""
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=30) as response:
            return (200 <= response.status < 300), (response.headers.get("Content-Type") or "").lower()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False, ""


def video_url(slug: str, filename: str) -> str:
    """組出影片的公開網址，並自動挑一個「真的能抓、而且回傳 video/*」的來源。"""
    slug = urllib.parse.quote(slug, safe="")
    filename = urllib.parse.quote(filename, safe="")
    explicit = os.environ.get("VIDEO_BASE_URL", "").strip().rstrip("/")
    candidates = []
    if explicit:
        candidates.append(f"{explicit}/posts/{slug}/{filename}")
    if REPO:
        owner, _, name = REPO.partition("/")
        ref = SHA or BRANCH
        if owner and name:
            # GitHub Pages 會用正確的 video/mp4 content-type，優先嘗試
            candidates.append(f"https://{owner}.github.io/{name}/posts/{slug}/{filename}")
        candidates.append(f"https://cdn.jsdelivr.net/gh/{REPO}@{ref}/posts/{slug}/{filename}")
        candidates.append(f"https://raw.githubusercontent.com/{REPO}/{ref}/posts/{slug}/{filename}")
    if not candidates:
        raise ApiError("沒有可用的影片網址來源（請設定 VIDEO_BASE_URL 或 GITHUB_REPOSITORY）")

    fallback = None
    for candidate in candidates:
        reachable, content_type = head_probe(candidate)
        if not reachable:
            print(f"   (skip) {candidate} -> unreachable")
            continue
        if content_type.startswith("video/") or content_type in ("application/octet-stream", "binary/octet-stream"):
            print(f"   video source: {candidate}  [{content_type}]")
            return candidate
        if fallback is None:
            fallback = (candidate, content_type)
        print(f"   (skip) {candidate} -> content-type {content_type or 'unknown'}")

    if fallback:
        print(f"   using fallback source {fallback[0]} (content-type {fallback[1] or 'unknown'})")
        return fallback[0]
    return candidates[0]


def load_posts() -> list[dict]:
    items = []
    for post_file in sorted(POSTS.glob("*/post.json")):
        try:
            data = json.loads(post_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            print(f"!! skip {post_file}: {error}")
            continue
        data["_file"] = post_file
        items.append(data)
    return items


def due(post: dict, now: datetime) -> bool:
    stamp = post.get("publish_time_iso")
    if not stamp:
        return False
    try:
        return datetime.fromisoformat(stamp) <= now
    except ValueError:
        return False


def published_today(posts: list[dict], now: datetime) -> int:
    day = now.strftime("%Y-%m-%d")
    return sum(1 for p in posts
               if p.get("status") == "published" and str(p.get("published_at") or "")[:10] == day)


def wait_ready(container_id: str) -> None:
    for _ in range(30):
        info = api("GET", f"/{container_id}", {"fields": "status_code,status"})
        status = info.get("status_code", "IN_PROGRESS")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise ApiError(f"container {container_id} status={status} {info.get('status') or ''}")
        time.sleep(10)
    raise ApiError(f"container {container_id} not ready after 5 minutes")


def publish(post: dict) -> str:
    url = video_url(post["id"], post["video"])
    print(f"   video: {url}")
    container = api("POST", f"/{IG_USER_ID}/media", {
        "media_type": "REELS",
        "video_url": url,
        "caption": post["caption"],
        "share_to_feed": "true",
    })["id"]
    wait_ready(container)
    return api("POST", f"/{IG_USER_ID}/media_publish", {"creation_id": container})["id"]


def save(post: dict) -> None:
    data = {k: v for k, v in post.items() if not k.startswith("_")}
    post["_file"].write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if not IG_USER_ID or not TOKEN:
        print("!! IG_USER_ID / IG_ACCESS_TOKEN are required (set them as repository secrets)")
        return 1

    now = datetime.now(timezone.utc).astimezone()
    posts = load_posts()
    done_today = published_today(posts, now)
    quota = MAX_POSTS if FORCE else max(min(MAX_POSTS, DAILY_CAP - done_today), 0)

    todo = [p for p in posts if p.get("status") != "published" and (FORCE or due(p, now))]
    todo.sort(key=lambda p: p.get("publish_time_iso") or "9999")
    print(f"posts={len(posts)} published_today={done_today} due={len(todo)} quota={quota} "
          f"dry_run={DRY_RUN} force={FORCE}")

    if not todo:
        print("nothing due")
        return 0
    if quota <= 0:
        print("daily cap reached")
        return 0

    ok = failed = 0
    for post in todo[:quota]:
        print(f"-> {post.get('publish_time_taipei')} {post.get('title')}")
        if DRY_RUN:
            print("   [dry-run] would publish")
            ok += 1
            continue
        try:
            media_id = publish(post)
            post["status"] = "published"
            post["media_id"] = media_id
            post["published_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            save(post)
            print(f"   published media_id={media_id}")
            ok += 1
        except ApiError as error:
            hint = ""
            if error.code == 190:
                hint = " (token invalid/expired -> refresh IG_ACCESS_TOKEN)"
            elif error.code in (4, 17, 32, 613) or error.status == 429:
                hint = " (rate limited -> retry later)"
            print(f"   FAILED {error}{hint}")
            failed += 1

    print(f"done: ok={ok} failed={failed}")
    return 0 if ok or not failed else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
