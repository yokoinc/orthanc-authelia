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
    // Labels of the injected entries, in the installation's language: the
    // « oe2 » section of translations/<lang>.json, served by auth-service. In
    // English until the response arrives, or if it fails -- the menu must never
    // wait for a translation before appearing.
    var TEXTES = { shares: "Shares", admin: "Administration", logout: "Sign out" };

    function libelle(cle) {
        return TEXTES[cle] || cle;
    }

    // Entries already placed keep their node; only their text changes.
    function rafraichirLibelles() {
        [["shares-injected", "shares"], ["admin-injected", "admin"], ["logout-fixe", "logout"]]
            .forEach(function (paire) {
                var el = document.getElementById(paire[0]);
                var texte = el && el.querySelector("[data-libelle]");
                if (texte) texte.textContent = libelle(paire[1]);
            });
    }

    fetch("/auth/static/oe2-menu-i18n.json", { credentials: "same-origin", cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
            if (d && d.textes) {
                TEXTES = Object.assign({}, TEXTES, d.textes);
                rafraichirLibelles();
            }
        })
        .catch(function () { /* English labels kept */ });

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

    function makeItem(id, glyph, cleLibelle, onClick) {
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
        // The label goes through textContent: it comes from a translation file,
        // it has no business being able to inject HTML.
        li.innerHTML =
            '<i class="' + iconFamilyOf(reference) + ' ' + glyph + ' fa-lg menu-icon" ' +
            'style="width:20px;min-width:20px;margin-right:10px;text-align:center"></i>' +
            '<span data-libelle></span> <span class="ms-auto"></span>';
        li.querySelector("[data-libelle]").textContent = libelle(cleLibelle);
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
        // Administrators only.
        //
        // This entry opens /auth/tokens/manage, which lists and REVOKES
        // everyone's shares: that is administration. Creating a share link is
        // a clinical act, done from a study's button, and has nothing to do
        // with it. Without this guard a doctor saw the entry and landed on a
        // 403.
        if (window.__OE2_IS_ADMIN__ !== true) return;
        place(makeItem("shares-injected", "fa-share-alt", "shares", function () {
            window.location.href = "/auth/tokens/manage";
        }), []);
    }

    function injectAdmin() {
        if (window.__OE2_IS_ADMIN__ !== true) return;
        var made = makeItem("admin-injected", "fa-cogs", "admin", function () {
            window.location.href = "/auth/admin";
        });
        if (!made) return;

        // Under the settings entry: it belongs with the configuration items,
        // not among the day-to-day ones.
        var settings = findItemByLabel(SETTINGS_LABELS);
        if (settings) {
            // After the settings SUBMENU, not after the entry. OE2 renders the
            // entry, then its submenu (System, Monitoring...) as the next
            // sibling -- <ul class="sub-menu collapse" id="settings-list">,
            // which the entry names in data-bs-target. Inserted straight after
            // the entry, Administration slid in between, and the settings
            // submenu opened under Administration.
            var anchor = settings;
            var target = settings.getAttribute("data-bs-target");
            var submenu = target && document.querySelector(target);
            if (submenu && submenu.parentNode === settings.parentNode) anchor = submenu;
            anchor.parentNode.insertBefore(made.li, anchor.nextSibling);
            return;
        }
        place(made, ["shares-injected"]);
    }

    /**
     * Sign-out: placed in <body>, NOT in the menu.
     *
     * The three previous attempts inserted it among OE2's <li> elements, and
     * each one broke something: random order, then an element outside the
     * <ul> that shifted the whole sidebar. The cause is always the same -- OE2
     * is a Vue application that rebuilds its menu whenever it sees fit, and
     * nothing slipped into it survives cleanly.
     *
     * So we stop fighting the re-render: the button lives outside Vue's tree,
     * in fixed position, and copies the sidebar's geometry. Vue never touches
     * what it did not create.
     *
     * Welcome side effect: no more dependency on the "Import" entry, which
     * accounts without upload rights do not have -- that is what deprived an
     * external account of any way to sign out.
     */
    function seDeconnecter() {
        // Authelia parses the body even when empty: without one, the call logs
        // "unable to parse body during logout".
        fetch("/api/logout", {
            method: "POST",
            credentials: "same-origin",
            headers: { "content-type": "application/json" },
            body: "{}",
        })
            .catch(function () { /* sign out locally even if the call fails */ })
            .then(function () { window.location.href = "/auth/"; });
    }

    function placerLogout() {
        var bouton = document.getElementById("logout-fixe");
        var menu = document.getElementById("menu-content");
        if (!menu) return;

        // The sidebar gives the position and the width. It is read every time
        // rather than hard-coding pixels: it changes with the window.
        var barre = menu.closest("nav, aside, .sidebar") || menu;
        var r = barre.getBoundingClientRect();
        if (r.width < 40) return;   // sidebar collapsed or not rendered yet

        if (!bouton) {
            bouton = document.createElement("div");
            bouton.id = "logout-fixe";
            bouton.innerHTML =
                '<i class="fa fa-sign-out-alt fa-lg" style="width:20px;min-width:20px;' +
                'margin-right:10px;text-align:center"></i><span data-libelle></span>';
            bouton.querySelector("[data-libelle]").textContent = libelle("logout");
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
