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
/* oe2-shared.css (loaded by index.html) is authoritative for the palette,
   the typography and the components. This file therefore only holds the
   style specific to the notification banner.

   The 13 :root variables and the body rule that used to sit here were
   removed: on top of the design system they redefined a different font
   (-apple-system instead of Avenir), a size of 13px instead of 14px, no
   light weight, and took the sidebar background as the page background. The
   panel therefore did not look like Orthanc Explorer even though the shared
   stylesheet was properly loaded. */

body {
  /* The only setting kept: oe2-shared.css does not reset the browser's
     default margin to zero. */
  margin: 0;
}
.flash {
  position: fixed;
  top: 12px; right: 12px; left: 12px;
  padding: 10px 14px;
  border-radius: var(--oe2-radius);
  font-size: var(--oe2-fs-small);
  z-index: 1000;
  max-width: 560px;
  margin-left: auto;
}
.flash--ok  { background: rgba(40,167,69,0.20); border-left: 3px solid var(--oe2-success); color: #b6f0c0; }
.flash--err { background: rgba(220,53,69,0.20); border-left: 3px solid var(--oe2-danger);  color: #ffb0b0; }
</style>
