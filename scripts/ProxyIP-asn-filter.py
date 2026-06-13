# ProxyIP高优筛选订阅
# 正常执行后会在当前目录下生成纯 IP 列表文件：ProxyIP-asn-ips.txt
# 格式为：ip:端口#地区-asn，包含 IPv4 和 IPv6

import json
import urllib.request
import urllib.error

def generate_ips_from_api():
    # ------------------ 配置区域 ------------------
    
    # 支持多国筛选：用集合(Set)定义你需要保留的国家代码
    # 如果不想限制国家，直接设置为 None 或者空集合 set() 即可
    TARGET_COUNTRY = {"HK", "SG", "TW", "JP", "KR", "US"}
    
    # 高优 ASN 筛选列表
    TARGET_ASNS = {
        906,
        3258,
        25820,
        32097,
        38136,
        49304,
        63888,
        10509,
        396982,
        137929,
        132203,
		153371,
        40065,
        63150,
        45102,
        400618,
        401443,
        400464,
		209242,
        140096,
        135064,
        4809,    # 电信 CN2
        9929,    # 联通 9929
        58453
    }
    
    TARGET_PORT = None

    # 数据源 API 接口
    API_URL = "https://zip.cm.edu.kg/all.json"
    
    # 输出文件名
    OUTPUT_FILENAME = "ProxyIP-asn-ips.txt"
    
    # ---------------------------------------------

    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    print(f"建立网络连接: 开始抓取目标接口数据 {API_URL} ...")
    try:
        req = urllib.request.Request(API_URL, headers=request_headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html_content = response.read().decode("utf-8")
            raw_data = json.loads(html_content)
    except urllib.error.URLError as e:
        print(f"网络连接失败: 无法访问 API 接口，错误原因: {e}")
        return
    except json.JSONDecodeError:
        print("数据解析失败: 接口返回的数据不是有效的 JSON 格式。")
        return
    except Exception as e:
        print(f"发生未知错误: {e}")
        return

    node_data_list = []
    if isinstance(raw_data, list):
        node_data_list = raw_data
    elif isinstance(raw_data, dict):
        for key, value in raw_data.items():
            if isinstance(value, list):
                node_data_list.extend(value)
            elif isinstance(value, dict):
                node_data_list.append(value)
    else:
        print("数据结构异常: 无法识别的 JSON 骨架类型。")
        return

    print(f"接口数据拉取成功: 准备分析总计 {len(node_data_list)} 条原始数据...")

    seen_ips = set()
    final_ip_lines = []

    for item in node_data_list:
        if not isinstance(item, dict):
            continue

        meta_data = item.get("meta", {})
        if not isinstance(meta_data, dict):
            meta_data = {}

        country = meta_data.get("country", "UNKNOWN")
        asn = meta_data.get("asn", "UNKNOWN")

        port = item.get("_port") or item.get("port")
        if isinstance(port, list) and len(port) > 0:
            port = port[0]

        if port is None:
            continue

        # 多国筛选过滤
        if TARGET_COUNTRY and country not in TARGET_COUNTRY:
            continue
            
        if TARGET_ASNS and asn not in TARGET_ASNS:
            continue
            
        if TARGET_PORT and int(port) != TARGET_PORT:
            continue
        
        v4_ip = item.get("ip")
        v6_ip = meta_data.get("clientIp")
        
        node_ips = []
        if v4_ip:
            node_ips.append(v4_ip)
        if v6_ip:
            node_ips.append(v6_ip)
            
        if not node_ips:
            continue
            
        for current_ip in node_ips:
            if current_ip in seen_ips:
                continue

            seen_ips.add(current_ip)
            
            # 如果是 IPv6 地址，包裹中括号
            if ":" in current_ip:
                formatted_ip = f"[{current_ip}]"
            else:
                formatted_ip = current_ip
                
            # 格式化输出为：ip:端口#地区-asn
            ip_line = f"{formatted_ip}:{port}#{country}-{asn}\n"
            final_ip_lines.append(ip_line)

    total_ips = len(final_ip_lines)
    print(f"多维匹配完成: 精确筛出并去重 {total_ips} 个独立合规 IP。")

    if total_ips == 0:
        print("未筛选到符合国家和 ASN 条件的优质节点，停止生成文件。")
        return

    # 统一写入单个目标文件
    try:
        with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
            f.writelines(final_ip_lines)
        print(f"文件保存成功: {OUTPUT_FILENAME} (共包含 {total_ips} 行数据)")
    except IOError as e:
        print(f"文件写入失败 {OUTPUT_FILENAME}: {e}")

if __name__ == "__main__":
    # 🟢 修复此处：移除了末尾错误的冒号
    generate_ips_from_api()
