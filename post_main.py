import requests

def post_content(content: str, api_key: str) -> bool:
    if not content or not api_key:
        print("❌ 内容或API Key为空")
        return False

    url = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
    
    headers = {
        "X-Square-OpenAPI-Key": api_key.strip(),
        "Content-Type": "application/json"
    }
    data = {
        "bodyTextOnly": content
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        json_res = response.json()
        
        if response.status_code == 200 and json_res.get("success") is True:
            print(f"✅ 发文成功！文章ID: {json_res.get('data')}")
            return True
        else:
            print(f"❌ 发文失败：{json_res}")
            return False
    except Exception as e:
        print(f"❌ 发文异常：{str(e)}")
        return False

def post_with_key(content, api_key):
    return post_content(content, api_key)
