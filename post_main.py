import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def get_session():
    """构建带重试机制的 Session"""
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

def upload_image_to_binance(file_path_or_bytes, api_key):
    """
    通过币安官方 OpenAPI 上传接口直传封面图片
    返回币安生成的官方 CDN 图片链接
    """
    try:
        session = get_session()
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "clienttype": "binanceSkill",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        if isinstance(file_path_or_bytes, bytes):
            files = {"file": ("cover.jpg", file_path_or_bytes, "image/jpeg")}
        else:
            if not os.path.exists(file_path_or_bytes):
                return ""
            files = {"file": ("cover.jpg", open(file_path_or_bytes, "rb"), "image/jpeg")}
            
        r = session.post(
            "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/file/upload",
            headers=headers,
            files=files,
            timeout=25
        )
        j = r.json()
        print("📥 币安官方图片上传接口返回:", j)
        if j.get("code") == "000000" or j.get("success"):
            data_info = j.get("data")
            if isinstance(data_info, dict):
                return data_info.get("url") or data_info.get("imageUrl") or data_info.get("fileUrl") or ""
            elif isinstance(data_info, str):
                return data_info
    except Exception as e:
        print("❌ 上传图片到币安官方接口失败:", e)
    return ""

def post_content(content, api_key):
    """发布短动态（进入【动态】分类）"""
    try:
        session = get_session()
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json",
            "clienttype": "binanceSkill",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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

def post_article(title, content, api_key, cover_file_bytes=None, cover_url=""):
    """长文章发布（优先通过币安官方通道直传图片）"""
    try:
        session = get_session()
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json",
            "clienttype": "binanceSkill",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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

        # 1. 如果传了图片二进制数据，优先调用币安官方接口上传
        final_cover_url = ""
        if cover_file_bytes:
            print("🚀 正在通过币安官方接口直传封面图片...")
            final_cover_url = upload_image_to_binance(cover_file_bytes, api_key)
            if final_cover_url:
                print(f"✅ 币安官方图片上传成功，直链: {final_cover_url}")

        # 2. 如果官方上传未返回或直接提供了外部 cover_url
        if not final_cover_url and cover_url:
            final_cover_url = cover_url.strip()

        if final_cover_url:
            data["cover"] = final_cover_url

        print("📦 最终发送给发文接口的 Payload:", data)

        r = session.post(
            "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
            headers=headers,
            json=data,
            timeout=30
        )
        j = r.json()
        print("📥 币安发文接口返回结果:", j)
        if j.get("code") == "000000" or j.get("success"):
            data_info = j.get("data")
            pid = data_info.get("id") if isinstance(data_info, dict) else data_info
            return True, "成功", pid
        return False, str(j), ""
    except Exception as e:
        return False, str(e), ""
