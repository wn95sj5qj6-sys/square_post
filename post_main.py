import requests

def post_content(content: str, api_key: str):
    if not content or not api_key:
        return False, "参数为空"
    url = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
    headers = {
        "X-Square-OpenAPI-Key": api_key.strip(),
        "Content-Type": "application/json"
    }
    data = {"bodyTextOnly": content}
    try:
        r = requests.post(url, headers=headers, json=data, timeout=15)
        j = r.json()
        if j.get("success"):
            return True, f"发文成功！文章ID：{j.get('data')}"
        else:
            return False, f"失败：{str(j)}"
    except Exception as e:
        return False, f"网络异常：{str(e)}"

def post_with_key(content, key):
    ok, _ = post_content(content, key)
    return ok
