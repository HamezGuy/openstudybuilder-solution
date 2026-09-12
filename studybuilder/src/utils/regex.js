function clearEmptyHtml(html) {
  // eslint-disable-next-line no-useless-escape
  const regex = new RegExp('^(?:\s*<[^>]+>\s*)+$')
  if (regex.test(html)) {
    return null
  }
  return html
}
export default {
  clearEmptyHtml,
}
