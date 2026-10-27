"""配置: 全部从环境变量读取 (见 .env.example)。"""
import os

DB_PATH = os.getenv("DB_PATH", "/data/grain.db")
# GPT 和网页访问接口用的密钥, 必填, 自己随便设一串长字符
API_KEY = os.getenv("API_KEY", "")
# 对外地址, 例如 https://xiaoliang.duckdns.org (写进 OpenAPI, GPT 动作要用)
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000")
# PushPlus 微信推送 token, 可留空 (留空则不推送)
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")
TZ = "Asia/Shanghai"

# 期货品种: 序列名 -> (新浪代码, 类型, 中文名, 单位)
FUTURES = {
    "futures:corn_dce": ("C0", "inner", "大商所玉米主力", "元/吨"),
    "futures:wheat_zce": ("WH0", "inner", "郑商所强麦主力", "元/吨"),
    "futures:corn_cbot": ("C", "global", "美玉米CBOT", "美分/蒲式耳"),
    "futures:wheat_cbot": ("W", "global", "美小麦CBOT", "美分/蒲式耳"),
}

# 河南主产区天气监测点 (城市: 纬度, 经度)
HENAN_CITIES = {
    "郑州": (34.75, 113.63),
    "周口": (33.62, 114.65),
    "驻马店": (32.98, 114.02),
    "商丘": (34.41, 115.66),
    "南阳": (33.00, 112.53),
    "新乡": (35.30, 113.93),
    "开封": (34.80, 114.31),
}

# 本地价格序列命名: local:<品种>:<地点>, 品种 corn / wheat
CROPS = {"corn": "玉米", "wheat": "小麦"}

HORIZONS = (7, 14, 30)  # 预测周期(天)
FLAT_PCT = 0.5          # 涨跌幅绝对值小于此值(%)算"平"
