import requests


def post_content(content, api_key):
  """发布短动态"""
  try:
    headers = {
        "X-Square-OpenAPI-Key": api_key.strip(),
        "Content-Type": "application/json",
        "clienttype": "binanceSkill",
    }
    data = {"bodyTextOnly": content.strip()}
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
  """发布文章长文（方案A：标题自动排版合并，纯文本标准提交）"""
  try:
    headers = {
        "X-Square-OpenAPI-Key": api_key.strip(),
        "Content-Type": "application/json",
        "clienttype": "binanceSkill",
    }

    # 将标题与多段落正文进行结构化排版
    if title and title.strip():
      full_article = f"📌 【{title.strip()}】\n\n{content.strip()}"
    else:
      full_article = content.strip()

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
