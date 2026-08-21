import os
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL_V1 = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi"
BASE_URL_V2 = "https://www.binance.com/bapi/composite/v2/public/pgc/openApi"

def get_session():
    """自动接管系统 VPN/代理的 Session"""
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

def upload_image_to_binance_official(file_path, api_key):
    """通过币安官方 v2 预签名通道上传图片并获取官方 CDN 直链"""
    if not os.path.exists(file_path):
        return ""

    session = get_session()
    headers = {
        "X-Square-OpenAPI-Key": api_key.strip(),
        "Content-Type": "application/json",
        "clienttype": "binanceSkill"
    }

    file_name = os.path.basename(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    mime_type = "image/png" if ext == ".png" else "image/webp" if ext == ".webp" else "image/jpeg"

    try:
        # 1. 预签名申请
        presign_res = session.post(
            f"{BASE_URL_V2}/image/presignedUrl",
            headers=headers,
            json={"imageName": file_name},
            timeout=15
        )
        presign_data = presign_res.json()
        if presign_data.get("code") != "000000" or not presign_data.get("data"):
            print(f"❌ 预签名申请失败: {presign_data}")
            return ""

        upload_url = presign_data["data"]["presignedUrl"]
        file_ticket = presign_data["data"]["fileTicket"]

        # 2. 直传二进制流
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        put_res = session.put(
            upload_url,
            headers={"Content-Type": mime_type},
            data=file_bytes,
            timeout=30
        )
        if not put_res.ok:
            print(f"❌ 二进制直传失败: {put_res.status_code}")
            return ""

        # 3. 轮询获取官方 CDN 链接
        for _ in range(15):
            time.sleep(1.2)
            status_res = session.post(
                f"{BASE_URL_V2}/image/imageStatus",
                headers=headers,
                json={"fileTicket": file_ticket},
                timeout=10
            )
            status_data = status_res.json()
            if status_data.get("code") == "000000" and status_data.get("data"):
                data_obj = status_data["data"]
                if data_obj.get("status") == 1 and data_obj.get("imageUrl"):
                    cdn_url = data_obj["imageUrl"]
                    print(f"✅ 官方 CDN 直链就绪: {cdn_url}")
                    return cdn_url

    except Exception as e:
        print(f"❌ 图片上传异常: {e}")

    return ""

def post_content(content, api_key, image_paths=None):
    """
    发布动态（短动态 / 多图动态，支持 1~4 张原生高清图）
    - 外部列表与点进详情页均能原生展示多图画廊
    """
    try:
        session = get_session()
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json",
            "clienttype": "binanceSkill"
        }

        uploaded_urls = []
        if image_paths:
            for img_path in image_paths[:4]: # 最多4张
                if os.path.exists(img_path):
                    url = upload_image_to_binance_official(img_path, api_key)
                    if url:
                        uploaded_urls.append(url)

        payload = {
            "bodyTextOnly": content.strip(),
            "contentType": 1
        }
        if uploaded_urls:
            payload["images"] = uploaded_urls
            payload["imageList"] = uploaded_urls

        print("📦 最终发送给【图文动态】接口的 Payload:", payload)
        r = session.post(f"{BASE_URL_V1}/content/add", headers=headers, json=payload, timeout=25)
        j = r.json()
        print("📥 币安动态发帖返回:", j)
        if j.get("code") == "000000" or j.get("success"):
            data_info = j.get("data")
            pid = data_info.get("id") if isinstance(data_info, dict) else data_info
            return True, "成功", pid
        return False, str(j), ""
    except Exception as e:
        return False, str(e), ""

def post_article(title, content, api_key, cover_path=""):
    """
    发布广场长文章（带 16:9 封面）
    - 外部列表展示封面卡片
    - 详情页展示纯净排版正文
    """
    try:
        session = get_session()
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json",
            "clienttype": "binanceSkill"
        }

        clean_title = title.strip()
        clean_content = content.strip()

        payload = {
            "title": clean_title,
            "bodyText": clean_content,
            "bodyTextOnly": f"{clean_title}\n\n{clean_content}",
            "contentType": 2,
            "format": "MARKDOWN"
        }

        # 上传封面图片至官方 CDN
        if cover_path and os.path.exists(cover_path):
            print("🚀 正在上传长文封面...")
            official_cover_url = upload_image_to_binance_official(cover_path, api_key)
            if official_cover_url:
                payload["cover"] = official_cover_url

        print("📦 最终发送给【广场长文】接口的 Payload:", payload)

        r = session.post(f"{BASE_URL_V1}/content/add", headers=headers, json=payload, timeout=30)
        j = r.json()
        print("📥 币安长文发帖返回:", j)
        if j.get("code") == "000000" or j.get("success"):
            data_info = j.get("data")
            pid = data_info.get("id") if isinstance(data_info, dict) else data_info
            return True, "成功", pid
        return False, str(j), ""
    except Exception as e:
        return False, str(e), ""
