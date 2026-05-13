import requests

def post_to_binance(content, api_key):
    if not api_key:
        return {"success": False, "msg": "币安API Key为空"}
    
    url = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
    headers = {
        "X-Square-OpenAPI-Key": api_key,
        "Content-Type": "application/json"
    }
    data = {"bodyTextOnly": content}
    
    try:
        res = requests.post(url, headers=headers, json=data, timeout=15)
        res.raise_for_status()
        # 直接返回币安的完整响应，不再自己组装结果
        return res.json()
    except Exception as e:
        return {"success": False, "msg": str(e)}
