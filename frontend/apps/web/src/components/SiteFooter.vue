<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { BRAND, brandName } from '@24goods/shared'
import { useSiteStore } from '../stores/site'

// Brand + the support address (#58): the ONLY place the storefront shows the contact email; policy
// pages point here instead of repeating it. White-label storefronts carry their own name, so the
// platform line is not shown there.
const { locale } = useI18n()
const site = useSiteStore()
</script>

<template>
  <footer v-if="!site.whiteLabel" class="site-footer muted">
    <i18n-t keypath="help.footer" tag="span">
      <template #brand>{{ brandName(locale) }}</template>
      <template #email><a :href="`mailto:${BRAND.supportEmail}`">{{ BRAND.supportEmail }}</a></template>
    </i18n-t>
  </footer>
</template>

<style scoped>
.site-footer {
  font-size: 12px;
  line-height: 18px;
  text-align: center;
  padding: 16px 16px 8px;
  overflow-wrap: anywhere;
}
.site-footer a {
  color: inherit;
  text-decoration: underline;
  display: inline-flex;
  align-items: center;
  min-height: 44px;
}
</style>
