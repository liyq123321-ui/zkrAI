const PRODUCT_SUFFIX = '(?:Web\\s*)?(?:应用|系统|平台|工具|服务|网站|小程序|客户端|后台|门户)';

export function conciseProjectTitle(value: string | null | undefined, fallback = '未命名项目') {
  const source = (value ?? '').replace(/\s+/g, ' ').trim();
  if (!source) return fallback;

  const cleaned = source
    .replace(/^(?:请)?(?:实现|开发|创建|构建|制作|设计|搭建|生成|新增)\s*(?:一个|一款|一套)?\s*/i, '')
    .replace(/^(?:可以|能够)?\s*(?:在)?本地运行的?\s*/i, '');
  const match = cleaned.match(new RegExp(`^(.{2,32}?${PRODUCT_SUFFIX})(?=[，。；;：:]|\\s{2,}|$)`, 'i'));
  const title = (match?.[1] ?? cleaned.split(/[，。；;：:\n]/, 1)[0] ?? cleaned)
    .replace(/\bweb\b/ig, 'Web')
    .trim();
  return title.length > 40 ? `${title.slice(0, 40).trim()}…` : title || fallback;
}
