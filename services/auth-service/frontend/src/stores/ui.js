// Store UI global : message flash affiche en haut de la page (succes/erreur),
// auto-hide apres 4s. Utilisable depuis n'importe quel composant :
//   const ui = useUiStore()
//   ui.notify('User cree', 'ok')
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
