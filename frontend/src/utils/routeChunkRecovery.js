const retryKey = 'studybuilder.route-chunk-retry'
const loadFailure =
  /loading chunk [\w-]+ failed|failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed|unable to preload css/i

export function retryRouteChunk(error, href, browser) {
  if (!loadFailure.test(error?.message) || browser.navigator.onLine === false)
    return
  const destination = new URL(href, browser.location.origin)
  if (destination.origin !== browser.location.origin) return
  // A persistent network failure must not create a navigation loop.
  try {
    if (browser.sessionStorage.getItem(retryKey) === destination.href) return
    browser.sessionStorage.setItem(retryKey, destination.href)
  } catch {
    return
  }
  browser.location.replace(destination.href)
}

export function clearRouteChunkRetry(href, browser) {
  try {
    if (
      browser.sessionStorage.getItem(retryKey) ===
      new URL(href, browser.location.origin).href
    ) {
      browser.sessionStorage.removeItem(retryKey)
    }
  } catch {
    // The page can render normally when session storage is unavailable.
  }
}
