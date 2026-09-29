"""Inline SVG icon set (24px grid, 1.5px stroke, round caps). One visual language for the whole product."""

from markupsafe import Markup

_P = {
    "logo": None,
    "overview": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
    "clients": '<path d="M4 20V8.5L12 4l8 4.5V20"/><path d="M9 20v-5h6v5"/><path d="M8.5 10.5h.01M12 10.5h.01M15.5 10.5h.01"/>',
    "review": '<path d="M9 11.5l2 2 4-4.5"/><path d="M12 3.5l7 3v5.2c0 4.3-3 7.4-7 8.8-4-1.4-7-4.5-7-8.8V6.5z"/>',
    "cases": '<path d="M4 7.5h16v11a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z"/><path d="M9 7.5V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5v2"/><path d="M4 12.5h16"/>',
    "leads": '<path d="M4 6.5h16v11H4z"/><path d="M4 7l8 6 8-6"/>',
    "rates": '<path d="M5 19L19 5"/><circle cx="7" cy="7" r="2.2"/><circle cx="17" cy="17" r="2.2"/>',
    "users": '<circle cx="9" cy="8.5" r="3.5"/><path d="M3 19.5c.8-3.2 3.1-5 6-5s5.2 1.8 6 5"/><path d="M16 5.2a3.5 3.5 0 0 1 0 6.6M18 14.8c1.5.7 2.6 2.3 3 4.7"/>',
    "audit": '<path d="M7 3.5h7.5L19 8v12.5H7z"/><path d="M14 3.5V8.5h5"/><path d="M10 13h6M10 16.5h6"/>',
    "design": '<circle cx="12" cy="12" r="8.5"/><circle cx="8.5" cy="10" r="1.2"/><circle cx="12" cy="7.5" r="1.2"/><circle cx="15.5" cy="10" r="1.2"/><path d="M12 20.5c-1.5 0-2-1-2-2s.8-2 2-2h1.5a3 3 0 0 0 3-3"/>',
    "logout": '<path d="M14 4.5h4.5v15H14"/><path d="M10 8l-4 4 4 4M6 12h9"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "upload": '<path d="M12 15.5V4.5M7.5 9L12 4.5 16.5 9"/><path d="M4.5 15v3.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V15"/>',
    "doc": '<path d="M7 3.5h7.5L19 8v12.5H7a1.5 1.5 0 0 1-1.5-1.5V5A1.5 1.5 0 0 1 7 3.5z"/><path d="M14 3.5V8.5h5"/>',
    "invoice": '<path d="M6 3.5h12v17l-2.5-1.5-2 1.5-1.5-1.2-1.5 1.2-2-1.5L6 20.5z"/><path d="M9 8h6M9 11.5h6M9 15h3.5"/>',
    "table": '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M3.5 9.5h17M3.5 14.5h17M9.5 9.5v10"/>',
    "meter": '<path d="M4.5 16.5a8 8 0 1 1 15 0"/><path d="M12 13.5l3.5-4"/><circle cx="12" cy="14" r="1.3"/>',
    "contract": '<path d="M7 3.5h10a1.5 1.5 0 0 1 1.5 1.5v15.5h-13V5A1.5 1.5 0 0 1 7 3.5z"/><path d="M9 8h6M9 11.5h6"/><path d="M9 16.5c1-1.2 1.8-1.2 2.5 0s1.5 1.2 2.5 0"/>',
    "rate": '<path d="M5 19L19 5"/><circle cx="7" cy="7" r="2.2"/><circle cx="17" cy="17" r="2.2"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 12.2l2.5 2.5 4.5-5"/>',
    "x-circle": '<circle cx="12" cy="12" r="8.5"/><path d="M9.3 9.3l5.4 5.4M14.7 9.3l-5.4 5.4"/>',
    "alert": '<path d="M12 4l9 15.5H3z"/><path d="M12 10v4M12 16.8h.01"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 7.8h.01"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "arrow-left": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "download": '<path d="M12 4.5v11M7.5 11l4.5 4.5 4.5-4.5"/><path d="M4.5 16v2.5A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5V16"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "play": '<path d="M8 5.5v13l10-6.5z"/>',
    "bolt": '<path d="M13 3L5.5 13.5H12L11 21l7.5-10.5H12z"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
    "trash": '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13"/>',
    "calendar": '<rect x="4" y="5.5" width="16" height="14.5" rx="1.5"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "euro": '<path d="M17.5 6.5a6.5 6.5 0 1 0 0 11"/><path d="M4.5 10.5h9M4.5 13.5h9"/>',
    "layers": '<path d="M12 4l8.5 4.5L12 13 3.5 8.5z"/><path d="M3.5 12.5L12 17l8.5-4.5"/>',
    "duplicate": '<rect x="8.5" y="8.5" width="11" height="11" rx="1.5"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
    "question": '<circle cx="12" cy="12" r="8.5"/><path d="M9.7 9.5a2.4 2.4 0 1 1 3.4 2.2c-.7.3-1.1.9-1.1 1.6v.4M12 16.8h.01"/>',
    "send": '<path d="M20.5 3.5L10 14M20.5 3.5L14 20.5l-4-6.5-6.5-4z"/>',
    "shield": '<path d="M12 3.5l7 3v5.2c0 4.3-3 7.4-7 8.8-4-1.4-7-4.5-7-8.8V6.5z"/>',
    "server": '<rect x="4" y="4.5" width="16" height="6" rx="1.5"/><rect x="4" y="13.5" width="16" height="6" rx="1.5"/><path d="M7.5 7.5h.01M7.5 16.5h.01"/>',
    "trace": '<path d="M3 15h3.5l2-6 3.5 10 2.5-7H21"/>',
    "calc": '<rect x="5" y="3.5" width="14" height="17" rx="1.5"/><path d="M8 7.5h8M8.5 12h.01M12 12h.01M15.5 12h.01M8.5 15.5h.01M12 15.5h.01M15.5 15.5h.01"/>',
    "row": '<rect x="3.5" y="8.5" width="17" height="7" rx="1.5"/><path d="M3.5 5h17M3.5 19h17"/>',
    "external": '<path d="M13.5 4.5h6v6M19.5 4.5L11 13"/><path d="M17 14v4.5A1.5 1.5 0 0 1 15.5 20h-10A1.5 1.5 0 0 1 4 18.5v-10A1.5 1.5 0 0 1 5.5 7H10"/>',
    "edit": '<path d="M14.5 5.5l4 4L8 20H4v-4z"/><path d="M12.5 7.5l4 4"/>',
    "target": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/>',
    "inbox": '<path d="M3.5 13.5l2.8-8h11.4l2.8 8v5a1.5 1.5 0 0 1-1.5 1.5h-14A1.5 1.5 0 0 1 3.5 18.5z"/><path d="M3.5 13.5H8l1.5 2.5h5l1.5-2.5h4.5"/>',
    "chart": '<path d="M4 20V4"/><path d="M4 20h16"/><path d="M8 16v-5M12 16V8M16 16v-3"/>',
    "user": '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20c.9-3.6 3.8-5.8 7.5-5.8s6.6 2.2 7.5 5.8"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "filter": '<path d="M4 5.5h16l-6.2 7.3v5.2l-3.6 1.5v-6.7z"/>',
    "sparkle": '<path d="M12 3.5l1.8 5.2 5.2 1.8-5.2 1.8L12 17.5l-1.8-5.2L5 10.5l5.2-1.8z"/><path d="M18.5 16l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    "flow": '<rect x="3.5" y="4" width="6" height="5" rx="1.2"/><rect x="14.5" y="15" width="6" height="5" rx="1.2"/><path d="M6.5 9v3.5a2 2 0 0 0 2 2h9v.5"/>',
    "phone": '<path d="M6.5 3.5h3l1.5 4-2 1.2a10 10 0 0 0 6.3 6.3l1.2-2 4 1.5v3a1.5 1.5 0 0 1-1.6 1.5A16.5 16.5 0 0 1 5 5.1 1.5 1.5 0 0 1 6.5 3.5z"/>',
    "globe": '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.3 2.4 3.5 5.2 3.5 8.5s-1.2 6.1-3.5 8.5c-2.3-2.4-3.5-5.2-3.5-8.5s1.2-6.1 3.5-8.5z"/>',
    "pin": '<path d="M12 20.5s-6.5-5.8-6.5-10.5a6.5 6.5 0 0 1 13 0c0 4.7-6.5 10.5-6.5 10.5z"/><circle cx="12" cy="10" r="2.3"/>',
    "settings":'<circle cx="12" cy="12" r="3"/><path d="M12 3.5v2.2M12 18.3v2.2M20.5 12h-2.2M5.7 12H3.5M18 6l-1.6 1.6M7.6 16.4L6 18M18 18l-1.6-1.6M7.6 7.6L6 6"/>',
}

LOGO = Markup(
    '<svg viewBox="0 0 28 28" aria-hidden="true" fill="none">'
    '<rect width="28" height="28" rx="7" fill="#0d1422"/>'
    '<path d="M5 17h4l2.4-7 3.2 10 2.4-6.5H23" stroke="#fff" stroke-width="1.9" stroke-linecap="round" '
    'stroke-linejoin="round"/>'
    '<circle cx="23" cy="13.5" r="2.2" fill="#6f8cff"/></svg>'
)


def icon(name: str, cls: str = "") -> Markup:
    body = _P.get(name) or _P["info"]
    klass = f' class="i {cls}"' if cls else ' class="i"'
    return Markup(
        f'<svg{klass} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{body}</svg>'
    )
