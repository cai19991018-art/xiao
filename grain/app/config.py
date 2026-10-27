"""配置: 全部从环境变量读取 (见 .env.example)。"""
import os

DB_PATH = os.getenv("DB_PATH", "/data/grain.db")
# 网页访问接口用的密钥, 必填, 自己随便设一串长字符
API_KEY = os.getenv("API_KEY", "")
# 自动分析开关 (1 开 / 0 关) 和每天运行时间 (北京时间)
AUTO_ANALYSIS = os.getenv("AUTO_ANALYSIS", "1") == "1"
AUTO_ANALYSIS_TIME = os.getenv("AUTO_ANALYSIS_TIME", "16:10")
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

PROVINCE = "河南"
# 本地价格序列命名: local:<品种>:<地点>, 品种 corn / wheat
CROPS = {"corn": "玉米", "wheat": "小麦"}
# 没有足够本地报价时, 用期货价折算成 元/斤 作参考
PROXY = {"corn": "futures:corn_dce", "wheat": "futures:wheat_zce"}
LOCAL_MIN_POINTS = 20   # 近 60 天本地报价达到这么多条才用本地价 (够统计模型用)

HORIZONS = (3, 7)       # 预测周期(天)
FLAT_PCT = 0.5          # 涨跌幅绝对值小于此值(%)算"平"

# DeepSeek (兼容 OpenAI 格式)。留空则不启用 AI 自动分析
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# deepseek-chat 便宜快速; 想要更深推理可改 deepseek-reasoner
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
