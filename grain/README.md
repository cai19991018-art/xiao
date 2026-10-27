# 河南粮价分析（玉米 / 小麦）

自用的粮价分析服务：日本 VPS 上自动采集行情，ChatGPT Plus 的「专属 GPT」负责分析，每次预测都会存档，到期自动对比实际价格，错了就复盘写进经验库，下次分析先读经验。

## 有什么功能

| 功能 | 说明 |
|---|---|
| 自动采集 | 大商所玉米、郑商所强麦、美国 CBOT 玉米/小麦日线（新浪），每天 7:40 / 15:40 / 23:40 |
| 河南天气 | 郑州、周口、驻马店、商丘、南阳、新乡、开封 未来 10 天降水和气温（Open-Meteo） |
| 本地价格 | 网页或对 GPT 说话即可记录本地收购价（元/斤），这是最重要的数据 |
| 统计模型 | 每天 16:10 对每个品种做 7/14/30 天预测（趋势 + 往年同期） |
| 自动复盘 | 到期自动对比实际价，按对错调整统计模型各信号的权重 |
| GPT 复盘 | GPT 预测错了，下次对话时它会先复盘并写入经验库 |
| 价格提醒 | 突破/跌破设定价时推送微信（PushPlus，可选） |

## 部署（日本 VPS，Ubuntu 22.04）

### 1. 申请免费域名（5 分钟）
1. 打开 https://www.duckdns.org ，用 GitHub 或谷歌账号登录。
2. 在 sub domain 填一个名字（如 `xiaoliang`），点 add domain。
3. current ip 填你 VPS 的公网 IP，点 update ip。得到 `xiaoliang.duckdns.org`。

### 2. 在 VPS 上部署
```bash
git clone -b claude/grain-price-analysis-app-n69zyd https://github.com/cai19991018-art/xiao.git
cd xiao/grain
bash deploy.sh          # 第一次: 安装 Docker, 生成 .env
nano .env               # 把 DOMAIN 和 PUBLIC_URL 改成你的 duckdns 域名
bash deploy.sh          # 第二次: 启动, 会打印 API Key
```
VPS 服务商后台的防火墙/安全组要放行 80 和 443 端口。

浏览器打开 `https://你的域名`，输入 API Key 即可看行情。第一次启动会自动采集，稍等一两分钟刷新。

### 3. 在 ChatGPT 里创建专属 GPT
1. 电脑浏览器打开 chatgpt.com → 左侧「GPT」→「创建」→「配置」。
2. 名称：粮价分析；说明（Instructions）：复制 `gpt/instructions.md` 全部内容。
3. 功能里勾选「网页搜索」。
4. 最下方「创建新动作」：
   - 身份验证 → API 密钥 → 认证类型选「自定义」，标头名称填 `X-API-Key`，密钥填你的 API Key。
   - 架构 → 「从 URL 导入」，填 `https://你的域名/openapi.json`。
   - 隐私政策填 `https://你的域名/`。
5. 右上角「创建」，可见范围选「只有我」。手机 ChatGPT 里也能用。

用法示例：
- 「今天周口小麦收购价 1.21」
- 「玉米接下来两周怎么走？我库里还有 80 吨，要不要卖？」
- 「玉米涨破 2400 提醒我」
- 「看看最近哪些预测错了」

## 日常维护
```bash
cd xiao/grain
git pull && docker compose up -d --build    # 更新
docker compose logs -f app                  # 看日志
cp data/grain.db ~/grain-backup-$(date +%F).db   # 备份数据
```

## 本地开发
```bash
pip install -r requirements.txt pytest httpx
python -m pytest -q
```
