<script setup>
import { useUiStore } from './stores/ui.js'
const ui = useUiStore()
</script>

<template>
  <div class="app">
    <div v-if="ui.msg" :class="['flash', 'flash--' + ui.kind]" role="status">
      {{ ui.msg }}
    </div>
    <router-view />
  </div>
</template>

<style>
:root {
  --oe2-nav-bg: rgb(30, 36, 42);
  --oe2-nav-sub-bg: #181c20;
  --oe2-nav-color: #ffffff;
  --oe2-accent: #3d748d;
  --oe2-accent-soft: #7fb3cf;
  --oe2-accent-orange: #d19b3d;
  --oe2-border-subtle: rgba(255, 255, 255, 0.2);
  --oe2-muted: #b0b0b0;
  --oe2-success: #28a745;
  --oe2-danger: #dc3545;
  --oe2-card-bg: rgb(40, 40, 45);
  --oe2-label-bg: rgb(61, 116, 141);
  --oe2-font-mono: 'Courier New', Consolas, monospace;
}
body {
  margin: 0;
  background: var(--oe2-nav-bg);
  color: var(--oe2-nav-color);
  font-family: -apple-system, "Segoe UI", Arial, sans-serif;
  font-size: 13px;
}
.flash {
  position: fixed;
  top: 12px; right: 12px; left: 12px;
  padding: 10px 14px;
  border-radius: 3px;
  font-size: 12px;
  z-index: 1000;
  max-width: 560px;
  margin-left: auto;
}
.flash--ok  { background: rgba(40,167,69,0.20); border-left: 3px solid var(--oe2-success); color: #b6f0c0; }
.flash--err { background: rgba(220,53,69,0.20); border-left: 3px solid var(--oe2-danger);  color: #ffb0b0; }
</style>
