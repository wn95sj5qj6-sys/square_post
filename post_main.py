import requests

def post_to_binance(content, api_key):
    if not api_key:
        return False, "币安API Key为空", None
    url = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
    headers = {
        "X-Square-OpenAPI-Key": api_key,
        "Content-Type": "application/json"
    }
    data = {"bodyTextOnly": content}
    try:
        res = requests.post(url, headers=headers, json=data, timeout=15)
        res.raise_for_status()
        j = res.json()
        if j.get("success"):
            return True, "发文成功", j.get("data", {}).get("postId")
        return False, j.get("message", "发文失败"), None
    except Exception as e:
        return False, str(e), None
