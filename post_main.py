import requests

def post_to_binance(content, api_key):
    if not api_key:
        return False, "API Key 不能为空", None

    url = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
    headers = {
        "X-Square-OpenAPI-Key": api_key,
        "Content-Type": "application/json"
    }
    data = {"bodyTextOnly": content}

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=15)
        resp.raise_for_status()
        result = resp.json()

        success = result.get("success", False)
        msg = result.get("message") or result.get("msg", "")
        post_id = result.get("data", {}).get("postId")
        return success, msg, post_id

    except Exception as e:
        return False, str(e), None
