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
