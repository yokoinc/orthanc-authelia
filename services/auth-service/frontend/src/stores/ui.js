// Global UI store: flash message shown at the top of the page (success or
// error), auto-hidden after 4s. Usable from any component:
//   const ui = useUiStore()
//   ui.notify('User created', 'ok')
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUiStore = defineStore('ui', () => {
  const msg = ref('')
  const kind = ref('ok')  // 'ok' | 'err'
  let timer = null

  function notify(text, k = 'ok') {
    msg.value = text
    kind.value = k
    clearTimeout(timer)
    timer = setTimeout(() => { msg.value = '' }, 4000)
  }

  function clear() {
    clearTimeout(timer)
    msg.value = ''
  }

  return { msg, kind, notify, clear }
})
