/**
 * "Pinned to the bottom" scroll helpers.
 *
 * Background sync (polled PRD progress, runtime summaries, workflow status)
 * re-renders views roughly every second, and the Agent's streaming writes
 * re-render the reading pane block by block. Scrolling to the bottom/target on
 * every render yanks the view away while the reader is browsing, so any
 * auto-scroll must first check whether the reader is still near the bottom.
 */

/** Distance (px) from the bottom within which a pane still counts as pinned. */
export const SCROLL_PIN_THRESHOLD_PX = 48;

export function isNearBottom(
  metrics: { scrollHeight: number; scrollTop: number; clientHeight: number },
  threshold = SCROLL_PIN_THRESHOLD_PX,
): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight < threshold;
}
