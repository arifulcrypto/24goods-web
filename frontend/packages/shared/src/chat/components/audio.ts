/** Only one voice message plays at a time (WeChat behaviour). */
let currentStop: (() => void) | null = null

export function claimAudio(stop: () => void): void {
  if (currentStop && currentStop !== stop) currentStop()
  currentStop = stop
}
export function releaseAudio(stop: () => void): void {
  if (currentStop === stop) currentStop = null
}
