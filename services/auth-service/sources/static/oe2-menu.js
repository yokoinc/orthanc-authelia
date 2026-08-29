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

    function injectLogout() {
        // No group gating: every signed-in user needs a way out. Authelia clears
        // the session on POST /api/logout; we then land on the portal, which
        // shows the login form.
        var made = makeItem("logout-injected", "fa-sign-out-alt", "Déconnexion", function () {
            // Authelia parses the body even when it carries nothing: without
            // it the call logs "unable to parse body during logout".
            fetch("/api/logout", {
                method: "POST",
                credentials: "same-origin",
                headers: { "content-type": "application/json" },
                body: "{}",
            })
                .catch(function () { /* log out locally even if the call fails */ })
                .then(function () { window.location.href = "/auth/"; });
        });
        if (!made) return;

        // JAMAIS d'ancrage sur l'entree « Parametres ».
        //
        // findItemByLabel la trouve dans UL#settings-list, une section
        // REPLIEE : la deconnexion inseree a cote disparaissait de l'ecran.
        // Et comme cette section n'existe pas encore au premier passage, le
        // resultat dependait de l'instant ou l'injection tombait -- visible une
        // fois, invisible la suivante. C'est cette course qui la faisait
        // manquer par intermittence. Mesure le 2026-08-29 : avec le script
        // d'origine, la deconnexion etait dans #settings-list, masquee.
        //
        // place() l'ancre apres #upload-handler, une entree de premier niveau
        // toujours affichee. Le placement est moins elegant, il est stable.
        place(made, ["shares-injected", "admin-injected"]);
    }

    function injectAll() {
        injectShares();
        injectAdmin();
        injectLogout();
    }

    new MutationObserver(injectAll).observe(document.documentElement, {
        childList: true,
        subtree: true,
    });
    document.addEventListener("DOMContentLoaded", function () {
        setTimeout(injectAll, 500);
    });
})();
