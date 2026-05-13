import requests

def post_to_binance(content, api_key):
    if not api_key:
        return False, "错误：币安API Key未配置", None
    try:
        headers = {
            "X-Square-OpenAPI-Key": api_key.strip(),
            "Content-Type": "application/json"
        }
        data = {"bodyTextOnly": content}
        r = requests.post(
            "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
            headers=headers,
            json=data,
            timeout=15
        )
        r.raise_for_status()
        j = r.json()
        if j.get("success"):
            return True, "发文成功", j.get("data", {}).get("postId")
        return False, j.get("message", "发文失败"), None
    except Exception as e:
        return False, f"请求异常：{str(e)}", None
