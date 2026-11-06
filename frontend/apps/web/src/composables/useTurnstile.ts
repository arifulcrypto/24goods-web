import { computed, ref } from 'vue'
import { ApiError, turnstileNeeded, type TurnstileAction } from '@24goods/shared'
import type { TurnstileWidget } from '@24goods/shared/ui'
import { useSiteStore } from '../stores/site'

/**
 * Turnstile state for one protected form (review-v5 §5.1). `needed` is false (and no script is
 * loaded) unless /site says Turnstile is enabled and `action` is protected.
 * Template: `<TurnstileWidget v-if="needed" ref="widget" v-model:token="token" :site-key="siteKey" :action="action" />`
 */
export function useTurnstile(action: TurnstileAction) {
  const site = useSiteStore()
  const token = ref('')
  const widget = ref<InstanceType<typeof TurnstileWidget> | null>(null)
  const needed = computed(() => turnstileNeeded(site.site, action))
  const siteKey = computed(() => site.site?.turnstile?.site_key ?? '')
  /** Token to send (undefined when the action is not protected). */
  function current(): string | undefined {
    return needed.value ? token.value || undefined : undefined
  }
  /** Throws the standard `turnstile_failed` error when a token is required but missing. */
  function require(): string | undefined {
    if (needed.value && !token.value) throw new ApiError('turnstile_failed', 'turnstile token missing', 400)
    return current()
  }
  /** Tokens are single use: call after every submit attempt. */
  function reset(): void {
    token.value = ''
    widget.value?.reset()
  }
  return { action, needed, siteKey, token, widget, current, require, reset }
}
