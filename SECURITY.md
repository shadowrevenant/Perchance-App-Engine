# Security notes

## Browser compatibility mode

Generator windows use normal Chromium certificate validation and process
isolation by default. The optional legacy Perchance compatibility mode weakens
those protections and is intended only for reproducible Perchance or
Cloudflare compatibility failures.

When compatibility mode is enabled:

- use generator windows only for the configured Perchance workflow;
- do not navigate them to unrelated or sensitive services;
- disable the mode and restart generator windows when it is no longer needed;
- do not distribute a settings file that enables the mode by default.

The offline TiddlyWiki window does not disable web security and does not allow
local content to access remote URLs.

## Sensitive data

The `data/` directory can contain persistent cookies, browser storage,
downloads, prompts, and gallery metadata. Do not commit or publicly distribute
it. Treat backups as private user data.

## Model files

The built-in tagger downloader validates exact size and SHA-256 before
installing a model. Run `python imagetools.py --verify` after manual copies.
