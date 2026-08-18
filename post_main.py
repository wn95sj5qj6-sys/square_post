import requests

def post_content(content, api_key):
    try:
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json"
        }
        data = {"bodyTextOnly": content}
        r = requests.post("https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
                          headers=headers, json=data, timeout=15)
        j = r.json()
        if j.get("success"):
            return True, "成功", j.get("data", "")
        return False, str(j), ""
    except Exception as e:
        return False, str(e), ""

import requests

def upload_image(image_file, api_key):
    """上传封面图片到币安广场，获取图片 URL"""
    try:
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip()
        }
        files = {
            "file": (image_file.filename, image_file.read(), image_file.content_type)
        }
        r = requests.post(
            "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/image/upload",
            headers=headers,
            files=files,
            timeout=30
        )
        j = r.json()
        if j.get("success"):
            data = j.get("data")
            if isinstance(data, dict):
                return True, data.get("url") or data.get("imageUrl") or "", "上传成功"
            elif isinstance(data, str):
                return True, data, "上传成功"
        return False, "", f"上传图片失败: {j}"
    except Exception as e:
        return False, "", str(e)

def post_content(content, api_key):
    """原有短动态发布"""
    try:
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json"
        }
        data = {"bodyTextOnly": content}
        r = requests.post("https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
                          headers=headers, json=data, timeout=15)
        j = r.json()
        if j.get("success"):
            return True, "成功", j.get("data", "")
        return False, str(j), ""
    except Exception as e:
        return False, str(e), ""

def post_article(title, content, cover_url, api_key):
    """发布长文/文章（含标题与封面）"""
    try:
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json"
        }
        data = {
            "title": title.strip(),
            "bodyText": content,
            "coverImgUrl": cover_url
        }
        if not cover_url:
            data.pop("coverImgUrl", None)

        r = requests.post(
            "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
            headers=headers,
            json=data,
            timeout=25
        )
        j = r.json()
        if j.get("success"):
            return True, "成功", j.get("data", "")
        return False, str(j), ""
    except Exception as e:
        return False, str(e), ""
