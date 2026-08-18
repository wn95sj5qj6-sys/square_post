import requests

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

def upload_image(image_file, api_key):
    """上传封面或正文配图到币安广场"""
    try:
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip()
        }
        # 重置文件指针
        image_file.seek(0)
        file_content = image_file.read()
        filename = getattr(image_file, 'filename', 'image.png')
        content_type = getattr(image_file, 'content_type', 'image/png')

        files = {
            "file": (filename, file_content, content_type)
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
        return False, "", f"上传失败: {j}"
    except Exception as e:
        return False, "", str(e)

def post_article(title, content, cover_url, api_key):
    """发布长文"""
    try:
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json"
        }
        data = {
            "title": title.strip(),
            "bodyText": content
        }
        if cover_url and cover_url.strip():
            data["coverImgUrl"] = cover_url.strip()

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
