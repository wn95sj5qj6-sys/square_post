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
  """长文发布：自动排版标题与多段落正文，通过标准接口提交"""
  try:
    headers = {
        "X-Square-OpenAPI-Key": api_key.strip(),
        "Content-Type": "application/json",
        "clienttype": "binanceSkill",
    }

    # 规范化组合标题与正文
    if title and title.strip():
      full_article = f"📌 【{title.strip()}】\n\n{content.strip()}"
    else:
      full_article = content.strip()

    # 必须使用官方唯一支持的 bodyTextOnly 字段
    data = {"bodyTextOnly": full_article}

    r = requests.post(
        "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add",
        headers=headers,
        json=data,
        timeout=25,
    )
    j = r.json()
    if j.get("code") == "000000" or j.get("success"):
      data_info = j.get("data")
      pid = data_info.get("id") if isinstance(data_info, dict) else data_info
      return True, "成功", pid
    return False, str(j), ""
  except Exception as e:
    return False, str(e), ""
