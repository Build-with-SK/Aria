# Glass frontend redesign

The owner chose direction C (Glass) on 2026-11-11, with further visual adjustments
to follow. This is the working base for those adjustments.

## Shared language

System fonts, translucent surfaces, quiet borders, generous spacing and restrained
motion. ARIA uses blue; SENTINEL uses amber. Both follow the operating system by
default and allow an explicit light/dark choice. Controls remain usable by touch
and keyboard. Reduced-motion preferences suppress decorative movement.

## Review before deployment

- ARIA: all eight workspaces, settings, sign-in and identity confirmation.
- SENTINEL: conversation, voice controls, brief, history, environment, system,
  approval confirmation, dashboard, background browser and Command/HOLO studio.
- Rating: Good saves immediately; Wrong opens an optional correction inline.
  Failed saves preserve the correction and provide a retry. Cancel sends no rating.
- Failed or withheld safety reads say unknown/unavailable. ARIA never infers paper,
  auto-execute off, released kill switch or an empty queue from a missing reply.
- Install manifests and phone/desktop icons are updated. Real installation on
  iPhone/iPad/Android/Windows remains a post-deployment device check.

## Validation scope

Frontend builds/offline renders and intercepted browser previews use sample data;
no live order, approval, device action or owner credential was exercised. The
existing sign-in and confirmation endpoint contracts are preserved. Browser
previews are evidence of UI behaviour, not of live service or broker health.

No backend or training source changed. No merge, push or server deployment is
included. Deployment requires the owner's explicit approval of a changed-files
bundle, followed by checks on the actual devices and live origin.

Deployed 2026-11-17 with the owner's approval: ARIA `64f2ce2` (frontend only) and
SENTINEL `ad99da0` (`face/` only), changed files, no restarts. The branches are
still not merged or pushed. Device installs remain the owner's check.
