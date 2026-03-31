#!/usr/bin/env python3
"""
Cloudflare 优选 IP 爬取脚本
目标: https://api.uouin.com/cloudflare.html
输出格式: 优选IP#线路  (例: 104.21.48.3#电信)
"""

import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── 可配置参数 ──────────────────────────────────────────────
TARGET_URL  = "https://api.uouin.com/cloudflare.html"
OUTPUT_FILE = Path("ips.txt")
TIMEOUT_MS  = 20_000   # 等待 XHR 响应最长时间(ms)
PAGE_WAIT_S = 8        # JS 渲染等待时间(秒)

# 线路名称标准化映射（将英文/缩写统一为中文）
ISP_ALIAS = {
    "telecom": "电信", "ct": "电信", "chinatelecom": "电信",
    "unicom":  "联通", "cu": "联通", "chinaunicom":  "联通",
    "mobile":  "移动", "cm": "移动", "chinamobile":  "移动",
    "cmcc":    "移动", "edu": "教育网", "cernet": "教育网",
}

CST = timezone(timedelta(hours=8))
# ────────────────────────────────────────────────────────────


def normalize_isp(raw: str) -> str:
    """将各种ISP表示统一为中文线路名"""
    key = raw.strip().lower()
    return ISP_ALIAS.get(key, raw.strip())


def extract_from_json(data) -> list[tuple[str, str]]:
    """
    从 JSON 数据中提取 (ip, 线路) 对。
    兼容多种常见响应结构：
      - {"data": [{"ip":"...","isp":"..."}]}
      - {"telecom":[...], "unicom":[...], "mobile":[...]}
      - [{"ip":"...", "line":"..."}]
    """
    results = []

    if isinstance(data, list):
        for item in data:
            ip  = item.get("ip") or item.get("IP") or item.get("addr", "")
            isp = (item.get("isp") or item.get("ISP") or
                   item.get("line") or item.get("type") or "")
            if ip and isp:
                results.append((ip.strip(), normalize_isp(isp)))
        return results

    if isinstance(data, dict):
        # 结构一：{"data": [...]}
        if "data" in data and isinstance(data["data"], list):
            return extract_from_json(data["data"])

        # 结构二：按线路分组的 dict，如 {"telecom":[...], "unicom":[...]}
        for key, val in data.items():
            if isinstance(val, list):
                isp_label = normalize_isp(key)
                for item in val:
                    if isinstance(item, dict):
                        ip = (item.get("ip") or item.get("IP") or
                              item.get("addr") or item.get("address", ""))
                    elif isinstance(item, str):
                        ip = item
                    else:
                        continue
                    if ip:
                        results.append((ip.strip(), isp_label))

    return results


def parse_html_table(html: str) -> list[tuple[str, str]]:
    """
    降级方案：直接解析 HTML 表格。
    麒麟检测页面通常有 <table> 包含 IP 和线路列。
    """
    from bs4 import BeautifulSoup
    results = []
    soup = BeautifulSoup(html, "lxml")

    # ① 先找所有 <table>
    for table in soup.find_all("table"):
        headers = []
        for th in table.find_all("th"):
            headers.append(th.get_text(strip=True).lower())

        # 尝试定位 ip 列和 线路/isp 列
        ip_col  = next((i for i, h in enumerate(headers)
                        if "ip" in h), None)
        isp_col = next((i for i, h in enumerate(headers)
                        if any(k in h for k in
                               ["线路", "isp", "运营商", "类型", "line"])), None)

        if ip_col is None:
            continue  # 这张表不含IP

        for tr in table.find_all("tr")[1:]:  # 跳过表头行
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            ip = cells[ip_col].get_text(strip=True) if ip_col < len(cells) else ""
            isp = (cells[isp_col].get_text(strip=True)
                   if isp_col is not None and isp_col < len(cells) else "未知")

            # 粗验证 IP 格式
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                results.append((ip, normalize_isp(isp)))

    # ② 若表格解析为空，尝试正则直接抓 IP + 附近文本
    if not results:
        ip_pattern = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
        isp_pattern = re.compile(r"(电信|联通|移动|教育网|telecom|unicom|mobile)", re.I)
        for match in ip_pattern.finditer(html):
            ip = match.group(1)
            # 在 IP 前后 60 字符窗口内找线路关键字
            window = html[max(0, match.start()-60): match.end()+60]
            m2 = isp_pattern.search(window)
            isp = normalize_isp(m2.group(1)) if m2 else "未知"
            results.append((ip, isp))

    return results


def fetch_with_playwright() -> list[tuple[str, str]]:
    """主方法：启动无头浏览器，优先拦截 XHR/Fetch 请求获取原始 JSON"""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    print(f"[{now()}] 启动 Playwright Chromium …")
    captured_json: list = []
    final_html = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-gpu", "--disable-extensions"]
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://api.uouin.com/"
            }
        )
        page = ctx.new_page()

        # ── 拦截所有 XHR/Fetch 响应 ──────────────────────────
        def handle_response(response):
            nonlocal captured_json
            url = response.url
            ct  = response.headers.get("content-type", "")
            # 只关心 JSON 类型的接口响应
            if "json" in ct and "cloudflare" in url.lower():
                try:
                    body = response.json()
                    items = extract_from_json(body)
                    if items:
                        print(f"  [XHR 拦截] {url}  → {len(items)} 条")
                        captured_json.extend(items)
                except Exception:
                    pass

        page.on("response", handle_response)

        # ── 打开页面并等待渲染 ────────────────────────────────
        try:
            page.goto(TARGET_URL, wait_until="networkidle",
                      timeout=TIMEOUT_MS)
        except PWTimeout:
            print(f"  [警告] networkidle 超时，继续尝试解析已有内容")

        print(f"  等待 JS 渲染 {PAGE_WAIT_S}s …")
        time.sleep(PAGE_WAIT_S)

        # 尝试滚动触发懒加载
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        final_html = page.content()
        browser.close()

    # ── 优先返回 XHR 拦截到的数据 ────────────────────────────
    if captured_json:
        print(f"[{now()}] XHR 拦截成功，共 {len(captured_json)} 条IP")
        return captured_json

    # ── 降级：解析渲染后的 HTML ──────────────────────────────
    print(f"[{now()}] XHR 未拦截到数据，降级解析 HTML …")
    results = parse_html_table(final_html)
    print(f"[{now()}] HTML 解析得到 {len(results)} 条IP")
    return results


def now() -> str:
    return datetime.now(CST).strftime("%H:%M:%S")


def write_output(results: list[tuple[str, str]]):
    """去重并写入 ips.txt，格式: IP#线路"""
    seen = set()
    lines = []
    for ip, isp in results:
        key = f"{ip}#{isp}"
        if key not in seen:
            seen.add(key)
            lines.append(key)

    # 按线路分组排序：电信 > 联通 > 移动 > 其他
    order = {"电信": 0, "联通": 1, "移动": 2}
    lines.sort(key=lambda x: (order.get(x.split("#")[-1], 9), x))

    header = (
        f"# Cloudflare 优选IP列表\n"
        f"# 来源: {TARGET_URL}\n"
        f"# 更新时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')} CST\n"
        f"# 格式: 优选IP#线路  共 {len(lines)} 条\n"
        f"# {'─'*45}\n"
    )
    OUTPUT_FILE.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"[{now()}] ✅ 已写入 {OUTPUT_FILE}，共 {len(lines)} 条（去重后）")


def main():
    print(f"[{now()}] ══ Cloudflare 优选IP 爬取开始 ══")
    try:
        results = fetch_with_playwright()
    except Exception as e:
        print(f"[{now()}] ❌ 抓取失败: {e}")
        sys.exit(1)

    if not results:
        print(f"[{now()}] ⚠️  未解析到任何IP，请检查页面结构是否变化")
        # 保留旧文件，不覆盖
        sys.exit(0)

    write_output(results)
    print(f"[{now()}] ══ 完成 ══")


if __name__ == "__main__":
    main()
