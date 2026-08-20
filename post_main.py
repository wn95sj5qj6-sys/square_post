import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def get_session():
    """构建带重试机制与标准 Headers 的 Session"""
    session = requests.Session()
    session.trust_env = True
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def post_content(content, api_key):
    """发布短动态（进入【动态】分类）"""
    try:
        session = get_session()
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json",
            "clienttype": "binanceSkill",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        data = {
            "bodyTextOnly": content.strip(),
            "contentType": 1
        }
        r = session.post(
            "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
            headers=headers,
            json=data,
            timeout=25
        )
        j = r.json()
        if j.get("code") == "000000" or j.get("success"):
            data_info = j.get("data")
            pid = data_info.get("id") if isinstance(data_info, dict) else data_info
            return True, "成功", pid
        return False, str(j), ""
    except Exception as e:
        return False, str(e), ""

def post_article(title, content, api_key, cover_url=""):
    """长文章发布（通过 Railway 自身生成的封面直链提交）"""
    try:
        session = get_session()
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json",
            "clienttype": "binanceSkill",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        clean_title = title.strip()
        clean_content = content.strip()

        data = {
            "title": clean_title,
            "bodyText": clean_content,
            "bodyTextOnly": f"{clean_title}\n\n{clean_content}",
            "contentType": 2,
            "format": "MARKDOWN"
        }

        # 携带服务自身生成的公网 HTTPS 封面直链
        if cover_url and cover_url.strip():
            data["cover"] = cover_url.strip()

        r = session.post(
            "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
            headers=headers,
            json=data,
            timeout=30
        )
        j = r.json()
        if j.get("code") == "000000" or j.get("success"):
            data_info = j.get("data")
            pid = data_info.get("id") if isinstance(data_info, dict) else data_info
            return True, "成功", pid
        return False, str(j), ""
    except Exception as e:
        return False, str(e), ""
