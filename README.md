# 🌩 Cloudflare 优选IP 自动采集

每 **30 分钟**自动从 [api.uouin.com/cloudflare.html](https://api.uouin.com/cloudflare.html) 抓取最新 Cloudflare 优选IP，整理为标准格式后提交至本仓库。

---

## 📄 输出文件

| 文件 | 说明 |
|------|------|
| `ips.txt` | 优选IP列表，格式 `IP#线路`，每次自动更新 |

**格式示例：**
```
104.21.48.3#电信
172.67.133.21#联通
108.162.215.9#移动
```
