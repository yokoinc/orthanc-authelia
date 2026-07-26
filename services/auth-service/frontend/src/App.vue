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
/* oe2-shared.css (charge par index.html) fait foi pour la palette, la
   typographie et les composants. Ce fichier ne contient donc que le style
   propre au bandeau de notification.

   Les 13 variables :root et la regle body qui se trouvaient ici ont ete
   retirees : elles redefinissaient par-dessus le design system une police
   differente (-apple-system au lieu d'Avenir), une taille de 13px au lieu
   de 14px, aucune graisse fine, et prenaient le fond de la barre laterale
   comme fond de page. Le panel ne ressemblait donc pas a Orthanc Explorer
   alors que la feuille partagee etait bien chargee. */

body {
  /* Seul reglage conserve : oe2-shared.css ne remet pas la marge par
     defaut du navigateur a zero. */
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
