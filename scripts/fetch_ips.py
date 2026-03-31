#!/usr/bin/env python3
"""
Cloudflare 优选 IP 爬取脚本
数据源1: https://ip.v2too.top        (requests 直接拉取)
数据源2: https://api.uouin.com/cloudflare.html (Playwright 无头浏览器)
输出格式: IP#线路  例: 162.159.45.187#电信
"""

import re
import sys
import time
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

# ── 可配置参数 ──────────────────────────────────────────────
OUTPUT_FILE  = Path("ips.txt")
TIMEOUT_MS   = 20_000
PAGE_WAIT_S  = 8
HTTP_TIMEOUT = 15

ISP_ALIAS = {
    "telecom": "电信", "ct": "电信", "chinatelecom": "电信",
    "unicom":  "联通", "cu": "联通", "chinaunicom":  "联通",
    "mobile":  "移动", "cm": "移动", "chinamobile":  "移动",
    "cmcc":    "移动", "edu": "教育网", "cernet": "教育网",
}
ISP_ORDER = {"电信": 0, "联通": 1, "移动": 2}
CST   = timezone(timedelta(hours=8))
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
# ────────────────────────────────────────────────────────────


def now() -> str:
    return datetime.now(CST).strftime("%H:%M:%S")


def normalize_isp(raw: str) -> str:
    key = raw.strip().lower()
    return ISP_ALIAS.get(key, raw.strip())


# ══════════════════════════════════════════════════════════
# 数据源 1: ip.v2too.top
# ══════════════════════════════════════════════════════════
def fetch_v2too() -> list[tuple[str, str]]:
    url = "https://ip.v2too.top"
    print(f"[{now()}] ── 数据源1: {url}")
    try:
        resp = requests.get(
            url,
            timeout=HTTP_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  [错误] 请求失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    results: list[tuple[str, str]] = []
    current_isp = "未知"

    texts = [t.strip() for t in soup.get_text(separator="\n").splitlines() if t.strip()]

    for line in texts:
        # 识别线路标题
        for isp_name in ["电信", "联通", "移动", "教育网"]:
            if isp_name in line and ("更新" in line or "时间" in line or "暂无" in line):
                current_isp = isp_name
                break
        # 识别 IP
        first = line.split()[0] if line.split() else ""
        if IP_RE.match(first):
            results.append((first, current_isp))

    print(f"  ✅ 获取到 {len(results)} 条IP")
    return results


# ══════════════════════════════════════════════════════════
# 数据源 2: api.uouin.com  (Playwright)
# ══════════════════════════════════════════════════════════
def extract_from_json(data) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    if isinstance(data, list):
        for item in data:
            ip  = item.get("ip") or item.get("IP") or item.get("addr", "")
            isp = (item.get("isp") or item.get("ISP") or
                   item.get("line") or item.get("type") or "")
            if ip and isp:
                results.append((ip.strip(), normalize_isp(isp)))
        return results
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            return extract_from_json(data["data"])
        for key, val in data.items():
            if isinstance(val, list):
                isp_label = normalize_isp(key)
                for item in val:
                    ip = ""
                    if isinstance(item, dict):
                        ip = (item.get("ip") or item.get("IP") or
                              item.get("addr") or item.get("address", ""))
                    elif isinstance(item, str):
                        ip = item
                    if ip:
                        results.append((ip.strip(), isp_label))
    return results


def parse_html_fallback(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    results: list[tuple[str, str]] = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        ip_col  = next((i for i, h in enumerate(headers) if "ip" in h), None)
        isp_col = next((i for i, h in enumerate(headers)
                        if any(k in h for k in ["线路","isp","运营商","类型","line"])), None)
        if ip_col is None:
            continue
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["td","th"])
            ip  = cells[ip_col].get_text(strip=True) if ip_col < len(cells) else ""
            isp = (cells[isp_col].get_text(strip=True)
                   if isp_col is not None and isp_col < len(cells) else "未知")
            if IP_RE.match(ip):
                results.append((ip, normalize_isp(isp)))
    if not results:
        isp_re = re.compile(r"(电信|联通|移动|教育网|telecom|unicom|mobile)", re.I)
        for m in re.finditer(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", html):
            ip = m.group(1)
            window = html[max(0, m.start()-60): m.end()+60]
            m2 = isp_re.search(window)
            isp = normalize_isp(m2.group(1)) if m2 else "未知"
            results.append((ip, isp))
    return results


def fetch_uouin() -> list[tuple[str, str]]:
    url = "https://api.uouin.com/cloudflare.html"
    print(f"[{now()}] ── 数据源2: {url}")
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("  [跳过] Playwright 未安装")
        return []

    captured: list[tuple[str, str]] = []
    final_html = ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox","--disable-dev-shm-usage",
                      "--disable-gpu","--disable-extensions"]
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Referer": "https://api.uouin.com/"
                }
            )
            page = ctx.new_page()

            def handle_response(response):
                ct = response.headers.get("content-type", "")
                if "json" in ct and "cloudflare" in response.url.lower():
                    try:
                        items = extract_from_json(response.json())
                        if items:
                            print(f"  [XHR] {response.url} → {len(items)} 条")
                            captured.extend(items)
                    except Exception:
                        pass

            page.on("response", handle_response)
            try:
                page.goto(url, wait_until="networkidle", timeout=TIMEOUT_MS)
            except PWTimeout:
                print("  [警告] networkidle 超时，继续解析")

            print(f"  等待JS渲染 {PAGE_WAIT_S}s …")
            time.sleep(PAGE_WAIT_S)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            final_html = page.content()
            browser.close()
    except Exception as e:
        print(f"  [错误] Playwright 运行失败: {e}")
        return []

    if captured:
        print(f"  ✅ XHR 拦截 {len(captured)} 条IP")
        return captured

    print("  XHR 未拦截到，降级解析HTML …")
    results = parse_html_fallback(final_html)
    print(f"  ✅ HTML 解析 {len(results)} 条IP")
    return results


# ══════════════════════════════════════════════════════════
# 合并 & 输出
# ══════════════════════════════════════════════════════════
def merge_and_write(all_results: list[tuple[str, str]]):
    seen: set[str] = set()
    lines: list[str] = []
    for ip, isp in all_results:
        if not IP_RE.match(ip):
            continue
        key = f"{ip}#{isp}"
        if key not in seen:
            seen.add(key)
            lines.append(key)

    # 排序：电信 > 联通 > 移动 > 其他
    lines.sort(key=lambda x: (ISP_ORDER.get(x.split("#")[-1], 9), x))

    ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"# Cloudflare 优选IP列表\n"
        f"# 数据源: ip.v2too.top + api.uouin.com\n"
        f"# 更新时间: {ts} CST\n"
        f"# 格式: IP#线路  共 {len(lines)} 条\n"
        f"# {'─'*45}\n"
    )
    OUTPUT_FILE.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[{now()}] ✅ 写入 {OUTPUT_FILE}，共 {len(lines)} 条（去重后）")

    stats: dict[str, int] = {}
    for line in lines:
        isp = line.split("#")[-1]
        stats[isp] = stats.get(isp, 0) + 1
    for isp, cnt in sorted(stats.items(), key=lambda x: ISP_ORDER.get(x[0], 9)):
        print(f"    {isp}: {cnt} 条")


def main():
    print(f"[{now()}] ══ Cloudflare 优选IP 合并采集开始 ══\n")
    all_results: list[tuple[str, str]] = []

    r1 = fetch_v2too()
    all_results.extend(r1)
    print()

    r2 = fetch_uouin()
    all_results.extend(r2)
    print()

    if not all_results:
        print(f"[{now()}] ⚠️  两个数据源均未获取到数据，保留旧文件")
        sys.exit(0)

    print(f"[{now()}] 合并前总计: {len(all_results)} 条（含重复）")
    merge_and_write(all_results)
    print(f"[{now()}] ══ 完成 ══")


if __name__ == "__main__":
    main()
