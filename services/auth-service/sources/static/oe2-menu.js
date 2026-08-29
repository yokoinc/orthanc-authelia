/**
 * oe2-menu.js — extra sidebar entries injected into Orthanc Explorer 2.
 *
 * Loaded by a one-line <script> tag that nginx injects into the OE2 page, which
 * also sets window.__OE2_IS_ADMIN__ from the $groups map.
 *
 * This used to live inline inside the nginx sub_filter replacement string, but
 * a config parameter cannot exceed 4096 bytes: the third entry pushed it over
 * and nginx refused to start with "too long parameter". Keeping the code here
 * removes that ceiling and makes it readable.
 *
 * OE2 is a Vue app that rebuilds its menu, so a MutationObserver re-applies the
 * entries whenever the DOM changes; each function is idempotent, guarded by the
 * id it inserts.
 */

(function () {
    /**
     * Clone the exact structure of an existing menu entry so the injected one
     * inherits OE2's classes and its scoped-style attribute (data-v-*), which is
     * what makes the styling apply at all.
     */
    // Font Awesome families, oldest to newest naming. Which one is loaded depends
    // on the OE2 build, and a glyph declared under the wrong family renders as an
    // empty box -- hence inheriting the family from a native entry rather than
    // hardcoding one.
    var ICON_FAMILIES = ["fa", "fas", "far", "fab", "fa-solid", "fa-regular", "fa-brands"];

    function iconFamilyOf(reference) {
        var icon = reference.querySelector("i");
        if (!icon) return "fa";
        var families = [];
        for (var i = 0; i < icon.classList.length; i++) {
            if (ICON_FAMILIES.indexOf(icon.classList[i]) !== -1) families.push(icon.classList[i]);
        }
        return families.length ? families.join(" ") : "fa";
    }

    function makeItem(id, glyph, label, onClick) {
        var menu = document.getElementById("menu-content");
        if (!menu || document.getElementById(id)) return null;
        var upload = document.getElementById("upload-handler");
        if (!upload) return null;
        var reference = upload.previousElementSibling;

        var li = document.createElement("li");
        li.id = id;
        li.className = reference.className;
        for (var i = 0; i < reference.attributes.length; i++) {
            var attr = reference.attributes[i];
            if (attr.name.startsWith("data-v-")) li.setAttribute(attr.name, attr.value);
        }
        li.innerHTML =
            '<i class="' + iconFamilyOf(reference) + ' ' + glyph + ' fa-lg menu-icon" ' +
            'style="width:20px;min-width:20px;margin-right:10px;text-align:center"></i>' + label +
            ' <span class="ms-auto"></span>';
        li.style.cursor = "pointer";
        li.addEventListener("click", onClick);
        return { li: li, after: upload };
    }

    function place(made, previousIds) {
        if (!made) return;
        // Keep a stable order: each entry lands after the last one already there.
        var anchor = made.after;
        for (var i = 0; i < previousIds.length; i++) {
            var existing = document.getElementById(previousIds[i]);
            if (existing) anchor = existing;
        }
        anchor.parentNode.insertBefore(made.li, anchor.nextSibling);
    }

    // The settings entry, in the languages OE2 ships. Matched on the label
    // because OE2 exposes no stable id for its own entries.
    var SETTINGS_LABELS = ["paramètre", "parametre", "setting", "einstellung"];

    /**
     * Find an existing OE2 entry by its visible label.
     *
     * OE2 builds its menu at runtime and does not expose stable ids for its own
     * entries, so matching on the label is what survives an OE2 upgrade. Several
     * spellings are accepted because the UI language follows the user's.
     * Returns null when nothing matches, and callers fall back to their default
     * position rather than dropping the entry.
     */
    function findItemByLabel(labels) {
        var items = document.querySelectorAll("#menu-content li");
        for (var i = 0; i < items.length; i++) {
            var text = (items[i].textContent || "").trim().toLowerCase();
            for (var j = 0; j < labels.length; j++) {
                if (text.indexOf(labels[j]) === 0) return items[i];
            }
        }
        return null;
    }

    function injectShares() {
        // Reserve aux administrateurs.
        //
        // Cette entree ouvre /auth/tokens/manage, qui liste et REVOQUE les
        // partages de tout le monde : c'est de l'administration. Creer un lien
        // de partage est un acte clinique, fait depuis le bouton d'une etude,
        // et n'a rien a voir. Sans ce garde-fou un medecin voyait l'entree et
        // tombait sur un 403.
        if (window.__OE2_IS_ADMIN__ !== true) return;
        place(makeItem("shares-injected", "fa-share-alt", "Partages", function () {
            window.location.href = "/auth/tokens/manage";
        }), []);
    }

    function injectAdmin() {
        if (window.__OE2_IS_ADMIN__ !== true) return;
        var made = makeItem("admin-injected", "fa-cogs", "Administration", function () {
            window.location.href = "/auth/admin";
        });
        if (!made) return;

        // Under the settings entry: it belongs with the configuration items,
        // not among the day-to-day ones.
        var settings = findItemByLabel(SETTINGS_LABELS);
        if (settings) {
            settings.parentNode.insertBefore(made.li, settings.nextSibling);
            return;
        }
        place(made, ["shares-injected"]);
    }

    /**
     * Deconnexion : posee dans <body>, PAS dans le menu.
     *
     * Les trois tentatives precedentes l'inseraient parmi les <li> d'OE2, et
     * chacune a casse quelque chose : ordre aleatoire, puis element hors de la
     * <ul> qui decalait toute la barre laterale. La cause est toujours la meme
     * -- OE2 est une application Vue qui reconstruit son menu quand bon lui
     * semble, et rien de ce qu'on y glisse n'y survit proprement.
     *
     * On cesse donc de lutter contre le re-rendu : le bouton vit en dehors de
     * l'arbre de Vue, en position fixe, et recopie la geometrie de la barre
     * laterale. Vue ne touche jamais a ce qu'il n'a pas cree.
     *
     * Effet de bord bienvenu : plus aucune dependance a l'entree « Importer »,
     * que les comptes sans droit de depot n'ont pas -- c'est ce qui privait un
     * compte externe de toute deconnexion.
     */
    function seDeconnecter() {
        // Authelia analyse le corps meme vide : sans lui, l'appel journalise
        // « unable to parse body during logout ».
        fetch("/api/logout", {
            method: "POST",
            credentials: "same-origin",
            headers: { "content-type": "application/json" },
            body: "{}",
        })
            .catch(function () { /* deconnecter localement meme si l'appel echoue */ })
            .then(function () { window.location.href = "/auth/"; });
    }

    function placerLogout() {
        var bouton = document.getElementById("logout-fixe");
        var menu = document.getElementById("menu-content");
        if (!menu) return;

        // La barre laterale donne la position et la largeur. On la lit a chaque
        // fois plutot que de figer des pixels : elle change avec la fenetre.
        var barre = menu.closest("nav, aside, .sidebar") || menu;
        var r = barre.getBoundingClientRect();
        if (r.width < 40) return;   // barre repliee ou pas encore rendue

        if (!bouton) {
            bouton = document.createElement("div");
            bouton.id = "logout-fixe";
            bouton.innerHTML =
                '<i class="fa fa-sign-out-alt fa-lg" style="width:20px;min-width:20px;' +
                'margin-right:10px;text-align:center"></i><span>Déconnexion</span>';
            bouton.style.cssText =
                "position:fixed;z-index:1030;cursor:pointer;display:flex;" +
                "align-items:center;padding:10px 16px;font-size:0.95rem;" +
                "color:#c9d1d9;background:transparent;border-top:1px solid rgba(255,255,255,0.08);";
            bouton.addEventListener("mouseenter", function () {
                bouton.style.background = "rgba(255,255,255,0.06)";
            });
            bouton.addEventListener("mouseleave", function () {
                bouton.style.background = "transparent";
            });
            bouton.addEventListener("click", seDeconnecter);
            document.body.appendChild(bouton);
        }
        bouton.style.left = r.left + "px";
        bouton.style.width = r.width + "px";
        bouton.style.bottom = "0px";
    }

    function injectAll() {
        injectShares();
        injectAdmin();
        placerLogout();
    }

    window.addEventListener("resize", placerLogout);

    new MutationObserver(injectAll).observe(document.documentElement, {
        childList: true,
        subtree: true,
    });
    document.addEventListener("DOMContentLoaded", function () {
        setTimeout(injectAll, 500);
    });
})();
