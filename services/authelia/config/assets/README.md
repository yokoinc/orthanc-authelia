# Customising the Authelia login page

This directory is Authelia's `asset_path` (`configuration.yml`, `server`
section). Authelia reads exactly three things from it, and nothing else:

```
assets/
├── favicon.ico                       tab icon
├── logo.png                          logo shown on the login page
└── locales/<language>/portal.json    portal texts
```

Reference: https://www.authelia.com/reference/guides/server-asset-overrides/

## logo.png — in place

A copy of the official Orthanc logo, so the login page and the PACS present
the same identity. Its presence flips `data-logooverride` to `true` in the
portal's HTML; Authelia then serves it at `/static/media/logo.png`. No setting
to enable.

## favicon.ico — possible, not done

Same principle. Dropping the file in is enough.

## locales/ — not recommended

Technically functional, but **the override replaces the whole file, it does
not merge**. Dropping in a `portal.json` holding a single sentence sends the
other 112 back to English. Customising one label therefore means copying all
113 strings into this repository, then resynchronising them with every
Authelia release — any string added upstream would stay hidden behind our
frozen copy.

One more trap: when a key contains a placeholder (`{{authelia}}`), the
translation must keep it. Otherwise Authelia refuses to start, in a restart
loop, with:

```
translation key '...' has a value which is missing a required placeholder
```

To fetch a language's complete file before editing it:

```bash
docker exec orthanc-nginx curl -s http://authelia:9091/locales/fr/portal.json
```

## CSS — not supported

Authelia has no custom stylesheet mechanism: a `custom.css` dropped here is
ignored. The only way is injection by nginx, already used in
`services/nginx/nginx.ssl.conf` (the `location /auth/` block) to hide the
password reset link.

## Theme

Independent of this directory: the `theme` option in `configuration.yml`
(`auto`, `light`, `dark`, `grey`).
