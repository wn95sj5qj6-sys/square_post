import requests

def post_content(content, api_key):
  """发布短动态（进入【动态】分类）"""
  try:
    headers = {
        "X-Square-OpenAPI-Key": api_key.strip(),
        "Content-Type": "application/json",
        "clienttype": "binanceSkill",
    }
    # 短动态专属字段
    data = {"bodyTextOnly": content.strip(), "contentType": 1}
    r = requests.post(
        "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
        headers=headers,
        json=data,
        timeout=15,
    )
    j = r.json()
    if j.get("code") == "000000" or j.get("success"):
      data_info = j.get("data")
      pid = data_info.get("id") if isinstance(data_info, dict) else data_info
      return True, "成功", pid
    return False, str(j), ""
  except Exception as e:
    return False, str(e), ""

def post_article(title, content, api_key):
  """币安广场新版 API 长文章标准发布接口（支持独立文章专栏归类）"""
  try:
    headers = {
        "X-Square-OpenAPI-Key": api_key.strip(),
        "Content-Type": "application/json",
        "clienttype": "binanceSkill",
    }

    clean_title = title.strip()
    clean_content = content.strip()

    # 官方升级版長文 Payload 规范
    data = {
        "title": clean_title,
        "bodyText": clean_content,  # 富文本/长文正文字段
        "bodyTextOnly": f"{clean_title}\n\n{clean_content}",  # 兼容性回退字段（彻底避免 220011 错误）
        "contentType": 2,  # 2 代表长文章（Article）
        "format": "MARKDOWN",
    }

    r = requests.post(
        "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
        headers=headers,
        json=data,
        timeout=30,
    )
    j = r.json()
    if j.get("code") == "000000" or j.get("success"):
      data_info = j.get("data")
      pid = data_info.get("id") if isinstance(data_info, dict) else data_info
      return True, "成功", pid
    return False, str(j), ""
  except Exception as e:
    return False, str(e), ""
