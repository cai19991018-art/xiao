# 粮见 · 河南玉米小麦行情分析

自用的粮价分析 App（手机网页，可添加到主屏幕）。行情采集、DeepSeek 分析、自动分析全部在后端运行，前端只负责展示和记录。

## 页面

| 页面 | 内容 |
|---|---|
| 行情 | 所选地区和品种的价格、走势图（1月/3月/1年）、记录本地收购价和一线见闻、期货参考、天气 |
| 分析 | 3天/7天走势判断（如「震荡偏强」），历史+预测趋势图、判断依据（利多/利空）、需关注的风险 |
| 复盘 | 已核验/待核验统计；每次预测「初始预测 → 实际走势」对比，判断一致/出现偏差，偏差原因；经验库 |
| 设置 | 关注地区、默认品种、价格单位（元/斤、元/吨）、预览演示/真实数据、后端接入状态 |

## 后端怎么运转

- **行情来源**：新浪期货日线（大商所玉米、郑商所强麦、CBOT），每天 7:40 / 15:40 / 23:40 采集；Open-Meteo 天气。
  本地收购价近 60 天记满 20 条后，该地区分析和核验自动改用本地价，否则用期货价 ÷ 2000 折算成元/斤。
- **DeepSeek**：在 `.env` 配置 Key 后，「生成分析」和自动分析都调用 DeepSeek；未配置或调用失败时自动退回统计模型。
- **自动分析**：每天 `AUTO_ANALYSIS_TIME`（默认 16:10）依次 ① 核验到期预测 ② DeepSeek 复盘出现偏差的预测，写偏差原因和经验 ③ 按「保存偏好」里的地区，生成玉米、小麦 3天/7天分析。
- **越用越准**：经验库和历史准确率会作为下次分析的输入；统计模型的趋势/季节信号权重按各自准确率自动调整。

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
iPhone 用 Safari 打开 → 分享 → 「添加到主屏幕」，就像一个 App。

### 3. 接入 DeepSeek
1. 打开 https://platform.deepseek.com 注册、充值（10 元能用很久），在「API keys」创建一个 Key。
2. 编辑 VPS 上的 `.env`，填入 `DEEPSEEK_API_KEY=sk-...`，然后 `docker compose up -d` 重启。
3. 打开「设置」页，DeepSeek 显示「已接入」即成功；到「分析」页点「生成分析」测试。
4. 在「设置」里选好关注地区，点「保存偏好」，自动分析就按这个地区运行。
   想要推理更深可把 `DEEPSEEK_MODEL` 改成 `deepseek-reasoner`（更慢、稍贵）。

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
