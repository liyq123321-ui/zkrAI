"""Derive one stable, human-sized title from a verbose project objective."""

import re


_LEADING_ACTION = re.compile(
    r"^(?:请|我们(?:需要|希望|要)?|希望|需要)?\s*"
    r"(?:实现|开发|创建|构建|建设|设计|制作|搭建|完成)\s*"
    r"(?:一个|一套|一种|一款)?\s*",
    re.IGNORECASE,
)
_LOCAL_QUALIFIER = re.compile(
    r"^(?:(?:可以|可)?(?:在)?本地运行的|本地运行的|可离线运行的)\s*",
    re.IGNORECASE,
)
_PRODUCT_ENDING = re.compile(
    r"^(.{2,36}?(?:(?:Web|WEB|web)\s*)?"
    r"(?:应用|系统|平台|网站|工具|服务|助手|小程序|门户|引擎|工作台|管理台|项目))"
    r"(?=$|[，,：:；;])",
    re.IGNORECASE,
)


def concise_project_title(value: object, fallback: str = "未命名项目") -> str:
    """Keep the product noun phrase and discard implementation/detail sentences."""

    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n。！？!?，,；;")
    if not text:
        return fallback
    first = re.split(r"[。！？!?\r\n]", text, maxsplit=1)[0].strip()
    first = _LEADING_ACTION.sub("", first).strip()
    first = _LOCAL_QUALIFIER.sub("", first).strip()
    product = _PRODUCT_ENDING.match(first)
    title = product.group(1) if product else re.split(
        r"[，,；;]|(?:用于|以便|从而|帮助|支持用户|用户可以)", first, maxsplit=1
    )[0]
    title = re.sub(r"\bweb\b", "Web", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" 的：:，,；;。")
    if not title:
        title = first or fallback
    return title[:40].rstrip()
