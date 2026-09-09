"""
Генератор артбордов макета приложения настроек.

Шесть экранов повторяют одну и ту же оболочку — боковое меню, шапку,
нижнюю панель, — поэтому она собирается кодом, а не копируется руками.
Значения цветов и размеров взяты из gui/theme/tokens.py без округлений.
"""

from pathlib import Path

OUT = Path(__file__).resolve().parent

LIGHT = dict(
    background='#F4F6F8', surface='#FFFFFF', surface_alt='#EDF0F4', surface_hover='#E4E9EF',
    border='#D5DAE1', border_strong='#B9C1CB',
    text='#16202B', muted='#5C6673', on_accent='#FFFFFF',
    accent='#2C5AA0', accent_hover='#24497F', accent_soft='#E3EAF6',
    success='#1A7F37', success_soft='#E6F4EA',
    warning='#A04100', warning_soft='#FAEEE4',
    danger='#B42318', danger_soft='#FBEAE8',
)

DARK = dict(
    background='#11161D', surface='#1A212B', surface_alt='#222B36', surface_hover='#2A3541',
    border='#2E3945', border_strong='#3D4956',
    text='#E3E8EE', muted='#97A1AD', on_accent='#0E141B',
    accent='#7FA8E2', accent_hover='#9BBCEA', accent_soft='#1E2A3A',
    success='#4FAF6E', success_soft='#17281D',
    warning='#D08A45', warning_soft='#2B2116',
    danger='#E37A6E', danger_soft='#2D1C1B',
)

FONT = "'Segoe UI', 'Segoe UI Variable', system-ui, sans-serif"

W, H = 1180, 800
NAV_W = 208

# ----------------------------------------------------------------------
# Значки — линейные, 16 px, одна толщина обводки
# ----------------------------------------------------------------------

ICONS = {
    'overview': '<path d="M2 9.5 L8 3.5 L14 9.5"/><path d="M4 8.5V13.5h8V8.5"/>',
    'queue': '<path d="M2.5 4.5h11"/><path d="M2.5 8h11"/><path d="M2.5 11.5h7"/>',
    'log': '<path d="M4 2.5h6l3 3v8H4z"/><path d="M9.5 2.5v3.5h3.5"/>',
    'plug': '<path d="M6 2.5v4"/><path d="M10 2.5v4"/><path d="M4 6.5h8v2a4 4 0 0 1-8 0z"/><path d="M8 12.5v2"/>',
    'fields': '<path d="M2.5 5.5h11v5h-11z"/><path d="M5 5.5v5"/>',
    'stages': '<path d="M2.5 3.5h11l-4 4.5v5l-3-1.5v-3.5z"/>',
    'sync': '<path d="M13 8a5 5 0 0 1-8.6 3.4"/><path d="M3 8a5 5 0 0 1 8.6-3.4"/><path d="M3 4.5V8h3.5"/><path d="M13 11.5V8H9.5"/>',
    'refresh': '<path d="M13 8a5 5 0 1 1-1.6-3.6"/><path d="M13 3v3.2h-3.2"/>',
    'folder': '<path d="M2.5 4.5h4l1.2 1.5h5.8v6.5h-11z"/>',
    'moon': '<path d="M13 9.6A5.5 5.5 0 0 1 6.4 3 5.6 5.6 0 1 0 13 9.6z"/>',
    'search': '<circle cx="7.2" cy="7.2" r="4"/><path d="M10.2 10.2 13.5 13.5"/>',
    'warning': '<path d="M8 2.6 14.4 13.4H1.6z"/><path d="M8 6.6v3.2"/><path d="M8 11.4v.2"/>',
    'check': '<path d="M3 8.4 6.4 11.8 13 5.2"/>',
    'cross': '<path d="M4 4l8 8"/><path d="M12 4l-8 8"/>',
    'dot': '<circle cx="8" cy="8" r="3.4"/>',
    'download': '<path d="M8 2.5v7.5"/><path d="M4.8 7.2 8 10.4l3.2-3.2"/><path d="M3 13h10"/>',
    'trash': '<path d="M3.5 4.5h9"/><path d="M6.5 4.5V3h3v1.5"/><path d="M4.8 4.5 5.4 13.5h5.2l.6-9"/>',
}


def icon(name, color, size=16, width=1.5, fill='none'):
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 16 16" fill="{fill}" '
        f'stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
        f'stroke-linejoin="round" style="flex: none">{ICONS[name]}</svg>'
    )


def dot(color, size=10):
    return (
        f'<span style="width: {size}px; height: {size}px; border-radius: 50%; '
        f'background: {color}; flex: none"></span>'
    )


# ----------------------------------------------------------------------
# Оболочка окна
# ----------------------------------------------------------------------

NAV_ITEMS = (
    ('НАБЛЮДЕНИЕ', (
        ('overview', 'Обзор'),
        ('queue', 'Очередь'),
        ('log', 'Журнал'),
    )),
    ('НАСТРОЙКА', (
        ('plug', 'Подключения'),
        ('fields', 'Поля'),
        ('stages', 'Стадии и воронка'),
        ('sync', 'Синхронизация'),
    )),
)


def nav(t, active, dirty=()):
    """Боковое меню: значок, подпись, пометка несохранённого"""
    parts = []

    for group, items in NAV_ITEMS:
        parts.append(
            f'<div style="padding: 16px 16px 4px; font-size: 11px; font-weight: 600; '
            f'letter-spacing: .04em; color: {t["muted"]}">{group}</div>'
        )

        for key, title in items:
            selected = key == active
            colour = t['accent'] if selected else t['text']
            background = t['accent_soft'] if selected else 'transparent'
            weight = '600' if selected else '400'

            mark = ''
            if key in dirty:
                mark = (
                    f'<span style="margin-left: auto; width: 7px; height: 7px; border-radius: 50%; '
                    f'background: {t["warning"]}" title="есть несохранённые правки"></span>'
                )

            bar = t['accent'] if selected else 'transparent'

            parts.append(
                f'<div style="display: flex; align-items: center; gap: 10px; padding: 7px 16px; '
                f'background: {background}; color: {colour}; font-weight: {weight}; '
                f'box-shadow: inset 3px 0 0 {bar}">'
                f'{icon(key, colour)}<span>{title}</span>{mark}</div>'
            )

    return (
        f'<div style="width: {NAV_W}px; flex: none; background: {t["surface_alt"]}; '
        f'border-right: 1px solid {t["border"]}; display: flex; flex-direction: column; '
        f'padding-bottom: 16px">{"".join(parts)}</div>'
    )


def header(t, workdir='C:\\Program Files\\IdentBitrix24'):
    return (
        f'<div style="display: flex; align-items: center; gap: 10px; padding: 0 16px; '
        f'height: 52px; flex: none; border-bottom: 1px solid {t["border"]}">'
        f'{icon("folder", t["muted"])}'
        f'<span style="color: {t["muted"]}; font-size: 12px">Папка службы</span>'
        f'<span style="color: {t["text"]}">{workdir}</span>'
        f'{button(t, "Выбрать…", "quiet")}'
        f'<span style="margin-left: auto"></span>'
        f'{icon_button(t, "moon", "Тёмная тема")}'
        f'</div>'
    )


def button(t, label, kind='normal', icon_name=None):
    styles = {
        'primary': f'background: {t["accent"]}; color: {t["on_accent"]}; '
                   f'border: 1px solid {t["accent"]}; font-weight: 600',
        'normal': f'background: {t["surface"]}; color: {t["text"]}; '
                  f'border: 1px solid {t["border_strong"]}',
        'quiet': f'background: transparent; color: {t["accent"]}; '
                 f'border: 1px solid transparent; font-weight: 600',
        'danger': f'background: {t["surface"]}; color: {t["danger"]}; '
                  f'border: 1px solid {t["danger"]}',
    }

    glyph = icon(icon_name, t['on_accent'] if kind == 'primary' else t['text']) if icon_name else ''
    gap = 'gap: 7px;' if icon_name else ''

    return (
        f'<button style="display: inline-flex; align-items: center; {gap} padding: 6px 14px; '
        f'border-radius: 5px; font-family: inherit; font-size: 13px; cursor: pointer; '
        f'{styles[kind]}">{glyph}<span>{label}</span></button>'
    )


def icon_button(t, name, title):
    return (
        f'<button title="{title}" style="display: inline-flex; align-items: center; '
        f'justify-content: center; width: 30px; height: 30px; border-radius: 5px; '
        f'background: transparent; border: 1px solid {t["border"]}; cursor: pointer">'
        f'{icon(name, t["muted"])}</button>'
    )


def card(t, body, padding=16):
    return (
        f'<div style="background: {t["surface"]}; border: 1px solid {t["border"]}; '
        f'border-radius: 5px; padding: {padding}px">{body}</div>'
    )


def card_title(t, text, extra=''):
    return (
        f'<div style="display: flex; align-items: center; gap: 8px; margin-bottom: 12px">'
        f'<span style="font-size: 15px; font-weight: 600; color: {t["text"]}">{text}</span>'
        f'{extra}</div>'
    )


def plate(t, tone, text, title=None):
    """Плашка сообщения — заметная, с цветом по смыслу"""
    colour = t[tone]
    background = t[f'{tone}_soft']
    glyph = {'success': 'check', 'warning': 'warning', 'danger': 'cross'}[tone]

    heading = (
        f'<div style="font-weight: 600; color: {colour}; margin-bottom: 2px">{title}</div>'
        if title else ''
    )

    return (
        f'<div style="display: flex; gap: 10px; padding: 10px 12px; background: {background}; '
        f'border: 1px solid {colour}; border-radius: 5px">'
        f'<span style="margin-top: 1px">{icon(glyph, colour)}</span>'
        f'<div style="color: {t["text"]}; line-height: 1.45">{heading}{text}</div></div>'
    )


def badge(t, text, tone=None):
    colour = t[tone] if tone else t['muted']
    background = t[f'{tone}_soft'] if tone else t['surface_alt']

    return (
        f'<span style="display: inline-flex; align-items: center; padding: 2px 8px; '
        f'border-radius: 3px; font-size: 12px; font-weight: 600; color: {colour}; '
        f'background: {background}">{text}</span>'
    )


def metric(t, label, value, note, tone=None):
    """Плитка показателя: подпись, крупное значение, пояснение"""
    colour = t[tone] if tone else t['text']
    marker = f'{dot(t[tone], 8)}' if tone else ''

    return (
        f'<div style="flex: 1; background: {t["surface"]}; border: 1px solid {t["border"]}; '
        f'border-radius: 5px; padding: 14px 16px; display: flex; flex-direction: column; gap: 4px">'
        f'<div style="display: flex; align-items: center; gap: 6px; font-size: 12px; '
        f'color: {t["muted"]}">{marker}<span>{label}</span></div>'
        f'<div style="font-size: 24px; font-weight: 600; color: {colour}; line-height: 1.15">{value}</div>'
        f'<div style="font-size: 12px; color: {t["muted"]}">{note}</div></div>'
    )


def field(t, label, control, hint=None, label_width=150):
    hint_html = (
        f'<div style="font-size: 12px; color: {t["muted"]}; margin-top: 4px">{hint}</div>'
        if hint else ''
    )
    return (
        f'<div style="display: flex; align-items: flex-start; gap: 12px">'
        f'<div style="width: {label_width}px; flex: none; padding-top: 6px; '
        f'color: {t["text"]}">{label}</div>'
        f'<div style="flex: 1; min-width: 0">{control}{hint_html}</div></div>'
    )


def text_input(t, value, width=320, placeholder=False, mono=False):
    colour = t['muted'] if placeholder else t['text']
    family = "'Consolas', 'Cascadia Mono', monospace" if mono else 'inherit'

    return (
        f'<div style="width: {width}px; padding: 5px 8px; min-height: 20px; '
        f'background: {t["surface"]}; border: 1px solid {t["border_strong"]}; '
        f'border-radius: 3px; color: {colour}; font-family: {family}">{value}</div>'
    )


def number_input(t, value, width=104):
    """Счётчик: значение слева, стрелки справа — то, чего сейчас не видно"""
    return (
        f'<div style="width: {width}px; display: flex; align-items: stretch; '
        f'background: {t["surface"]}; border: 1px solid {t["border_strong"]}; border-radius: 3px">'
        f'<div style="flex: 1; padding: 5px 8px; color: {t["text"]}">{value}</div>'
        f'<div style="width: 18px; flex: none; border-left: 1px solid {t["border"]}; '
        f'display: flex; flex-direction: column">'
        f'<div style="flex: 1; display: flex; align-items: center; justify-content: center; '
        f'border-bottom: 1px solid {t["border"]}">'
        f'<svg width="8" height="5" viewBox="0 0 8 5" fill="none" stroke="{t["muted"]}" '
        f'stroke-width="1.4" stroke-linecap="round"><path d="M1 4 4 1 7 4"/></svg></div>'
        f'<div style="flex: 1; display: flex; align-items: center; justify-content: center">'
        f'<svg width="8" height="5" viewBox="0 0 8 5" fill="none" stroke="{t["muted"]}" '
        f'stroke-width="1.4" stroke-linecap="round"><path d="M1 1 4 4 7 1"/></svg></div>'
        f'</div></div>'
    )


def select_input(t, value, width=260):
    return (
        f'<div style="width: {width}px; display: flex; align-items: center; '
        f'background: {t["surface"]}; border: 1px solid {t["border_strong"]}; '
        f'border-radius: 3px; padding: 5px 8px">'
        f'<span style="color: {t["text"]}">{value}</span>'
        f'<svg width="10" height="6" viewBox="0 0 10 6" fill="none" stroke="{t["muted"]}" '
        f'stroke-width="1.5" stroke-linecap="round" style="margin-left: auto">'
        f'<path d="M1 1 5 5 9 1"/></svg></div>'
    )


def shell(t, nav_html, header_html, content_html, footer_html):
    return (
        f'<div style="width: {W}px; height: {H}px; display: flex; background: {t["background"]}; '
        f'color: {t["text"]}; font-family: {FONT}; font-size: 13px; overflow: hidden">'
        f'{nav_html}'
        f'<div style="flex: 1; display: flex; flex-direction: column; min-width: 0">'
        f'{header_html}'
        f'<div style="flex: 1; padding: 16px; display: flex; flex-direction: column; '
        f'gap: 12px; min-height: 0; overflow: hidden">{content_html}</div>'
        f'{footer_html}</div></div>'
    )


def footer(t, left, right=''):
    return (
        f'<div style="display: flex; align-items: center; gap: 10px; padding: 10px 16px; '
        f'flex: none; border-top: 1px solid {t["border"]}; background: {t["surface_alt"]}">'
        f'<div style="color: {t["muted"]}; font-size: 12px">{left}</div>'
        f'<div style="margin-left: auto; display: flex; gap: 8px">{right}</div></div>'
    )


def document(body):
    return (
        '<!doctype html>\n<html>\n<head>\n  <meta charset="utf-8">\n'
        '  <script src="./support.js"></script>\n</head>\n<body>\n<x-dc>\n'
        '<helmet>\n  <style>\n'
        '    body { margin: 0; }\n'
        '    a { color: #2C5AA0; } a:hover { color: #24497F; }\n'
        '    button { font-family: inherit; }\n'
        '  </style>\n</helmet>\n'
        f'{body}\n</x-dc>\n</body>\n</html>\n'
    )


def write(name, body):
    (OUT / name).write_text(document(body), encoding='utf-8')
    print(f'  {name}')


# ----------------------------------------------------------------------
# Экран «Обзор»
# ----------------------------------------------------------------------

def service_card(t, running: bool):
    """Крупная карточка службы — первое, что читает администратор"""
    if running:
        tone, title = 'success', 'Служба работает'
        note = 'Задача планировщика \\IDENT\\IdentBitrix24Integration, процесс 4218'
        actions = button(t, 'Остановить', 'normal') + button(t, 'Перезапустить', 'normal')
    else:
        tone, title = 'danger', 'Служба остановлена'
        note = 'Последняя попытка запуска завершилась кодом 1 — смотрите журнал за 09:12'
        actions = button(t, 'Запустить', 'primary') + button(t, 'Перезапустить', 'normal')

    facts = (
        ('Последний запуск', '09.09.2026 13:42:06' if running else '09.09.2026 09:12:41'),
        ('Следующий запуск', '09.09.2026 13:44:00' if running else '—'),
        ('Результат', 'код 0' if running else 'код 1 — ошибка'),
        ('Процессов', '1' if running else '0'),
    )

    facts_html = ''.join(
        f'<div style="display: flex; flex-direction: column; gap: 2px">'
        f'<span style="font-size: 12px; color: {t["muted"]}">{label}</span>'
        f'<span style="color: {t["text"]}">{value}</span></div>'
        for label, value in facts
    )

    return card(t,
        f'<div style="display: flex; align-items: center; gap: 12px">'
        f'{dot(t[tone], 12)}'
        f'<div style="display: flex; flex-direction: column; gap: 2px">'
        f'<span style="font-size: 17px; font-weight: 600; color: {t["text"]}">{title}</span>'
        f'<span style="font-size: 12px; color: {t["muted"]}">{note}</span></div>'
        f'<div style="margin-left: auto; display: flex; gap: 8px">{actions}</div></div>'
        f'<div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; '
        f'margin-top: 16px; padding-top: 14px; border-top: 1px solid {t["border"]}">{facts_html}</div>'
    )


def overview(t, alarm: bool):
    if alarm:
        metrics = (
            metric(t, 'Отставание синхронизации', '3 ч 04 мин',
                   'данные обработаны до 10:38', 'danger'),
            metric(t, 'В очереди', '47 записей',
                   'ожидают отправки в Битрикс24', 'warning'),
            metric(t, 'Попытки исчерпаны', '12 записей',
                   'служба их больше не возьмёт', 'danger'),
        )
        problems = card(t,
            card_title(t, 'Замечания к конфигурации',
                       badge(t, '2', 'warning')) +
            f'<div style="display: flex; flex-direction: column; gap: 8px">'
            + plate(t, 'danger', 'Служба не сможет прочитать пароль базы данных, '
                                 'пока модуль не установлен.',
                    'В конфигурации есть зашифрованные значения, но pywin32 не установлен')
            + plate(t, 'warning', 'Стадия <b>UC_NO40X0</b> указана и в финальных, и в защищённых — '
                                  'сделки в ней перестанут обновляться.',
                    'Стадия перечислена дважды')
            + '</div>'
        )
    else:
        metrics = (
            metric(t, 'Отставание синхронизации', '4 минуты',
                   'данные обработаны до 13:42', 'success'),
            metric(t, 'В очереди', 'пусто',
                   'все записи доставлены', 'success'),
            metric(t, 'Попытки исчерпаны', 'нет', 'разбирать нечего'),
        )
        problems = card(t,
            card_title(t, 'Замечания к конфигурации') +
            plate(t, 'success', 'Проверены 82 параметра в 19 разделах.',
                  'Замечаний нет')
        )

    content = (
        service_card(t, running=not alarm)
        + f'<div style="display: flex; gap: 12px">{"".join(metrics)}</div>'
        + f'<div style="flex: 1; display: flex; min-height: 0">'
        + problems.replace('border-radius: 5px; padding: 16px"',
                           'border-radius: 5px; padding: 16px; flex: 1"', 1)
        + '</div>'
    )

    return shell(
        t,
        nav(t, 'overview'),
        header(t),
        content,
        footer(t, 'Обновлено 3 секунды назад · обновляется каждые 10 секунд',
               button(t, 'Обновить', 'normal', 'refresh')),
    )


# ----------------------------------------------------------------------
# Экран «Подключения»
# ----------------------------------------------------------------------

def connections(t):
    """
    Две карточки рядом, а не одна под другой.

    Окно шириной 1180 даёт под содержимое 940 пикселей — в один столбец
    формы уходят за нижний край, и подсказка под последним полем обрезается
    областью прокрутки. Рядом обе помещаются целиком.
    """
    database = card(t,
        card_title(t, 'База данных Ident')
        + '<div style="display: flex; flex-direction: column; gap: 8px">'
        + field(t, 'Сервер', text_input(t, 'SQL-01\\IDENT', 250))
        + field(t, 'Порт', number_input(t, '1433'))
        + field(t, 'База данных', text_input(t, 'IdentDB', 200))
        + field(t, 'Пользователь', text_input(t, 'ident_reader', 200))
        + field(t, 'Пароль', text_input(t, '•••••••••', 200))
        + field(t, 'Таймаут подключения', number_input(t, '10 с'))
        + field(t, 'Таймаут запроса', number_input(t, '30 с'))
        + '</div>'
        + f'<div style="margin-top: 12px">'
        + plate(t, 'warning',
                'Служба его прочитает, но в config.ini он лежит открытым текстом. '
                'Введите пароль заново — приложение зашифрует его через DPAPI.',
                'Пароль хранится незашифрованным')
        + '</div>'
        + f'<div style="display: flex; flex-direction: column; gap: 10px; margin-top: 12px">'
        + button(t, 'Проверить подключение', 'normal')
        + plate(t, 'success', 'Подключение к базе IdentDB работает, ответ за 38 мс.')
        + '</div>'
    )

    portal = card(t,
        card_title(t, 'Портал Битрикс24')
        + '<div style="display: flex; flex-direction: column; gap: 8px">'
        + field(t, 'Адрес вебхука',
                f'<div style="display: flex; gap: 6px; align-items: center">'
                f'{text_input(t, "https://clinic.bitrix24.ru/rest/12/••••••••/", 250, mono=True)}'
                f'{button(t, "Показать", "quiet")}</div>')
        + field(t, 'Ответственный', text_input(t, 'пусто — владелец вебхука', 200, placeholder=True))
        + field(t, 'Таймаут запроса', number_input(t, '30 с'))
        + field(t, 'Повторов при ошибке', number_input(t, '3'))
        + field(t, 'Ограничение частоты', number_input(t, '2 запр./с'))
        + '</div>'
        + f'<div style="margin-top: 12px">'
        + plate(t, 'warning',
                'Адрес вебхука содержит секретный ключ портала — не передавайте его третьим лицам. '
                'Права вебхука должны включать CRM.')
        + '</div>'
        + f'<div style="display: flex; flex-direction: column; gap: 10px; margin-top: 12px">'
        + button(t, 'Проверить подключение', 'normal')
        + plate(t, 'danger',
                'Портал ответил 401: вебхук не принят. Проверьте, не отозван ли ключ '
                'и включает ли он права CRM.')
        + '</div>'
    )

    content = (
        f'<div style="display: flex; gap: 12px; align-items: flex-start">'
        f'<div style="flex: 1; min-width: 0">{database}</div>'
        f'<div style="flex: 1; min-width: 0">{portal}</div></div>'
    )

    return shell(
        t,
        nav(t, 'plug', dirty=('plug',)),
        header(t),
        content,
        footer(t,
               'Несохранённых изменений: 2 — сервер базы данных, таймаут запроса',
               button(t, 'Отменить изменения', 'normal')
               + button(t, 'Сохранить настройки', 'primary')),
    )


# ----------------------------------------------------------------------
# Экран «Очередь»
# ----------------------------------------------------------------------

QUEUE_ROWS = (
    ('F1_18452', 'Иванова Мария Сергеевна', 'Ошибка', 'danger', '5',
     '09.09 10:41', 'HTTPSConnectionPool: время ожидания истекло'),
    ('F1_18449', 'Петров Артём Игоревич', 'Ошибка', 'danger', '5',
     '09.09 10:41', 'HTTPSConnectionPool: время ожидания истекло'),
    ('F1_18447', 'Соколова Анна Дмитриевна', 'Ожидает', 'warning', '2',
     '09.09 10:39', 'QUERY_LIMIT_EXCEEDED: превышено число запросов'),
    ('F2_09113', 'Кузнецов Павел Олегович', 'Ожидает', 'warning', '1',
     '09.09 10:38', 'QUERY_LIMIT_EXCEEDED: превышено число запросов'),
    ('F1_18440', 'Морозова Елена Викторовна', 'Ошибка', 'danger', '5',
     '09.09 10:32', 'Сделка не найдена: uf_crm_ident_id пуст'),
    ('F2_09108', 'Григорьев Илья Андреевич', 'Ожидает', 'warning', '3',
     '09.09 10:30', 'HTTPSConnectionPool: время ожидания истекло'),
    ('F1_18431', 'Новикова Ольга Павловна', 'Отправлено', 'success', '2',
     '09.09 10:22', '—'),
)


def queue(t):
    head_cells = ('Запись Ident', 'Пациент', 'Статус', 'Попыток', 'Обновлена', 'Последняя ошибка')
    widths = ('120px', '230px', '110px', '84px', '116px', 'minmax(0, 1fr)')

    header_row = ''.join(
        f'<div style="padding: 8px 10px; font-size: 12px; font-weight: 600; '
        f'color: {t["muted"]}">{cell}</div>'
        for cell in head_cells
    )

    rows = ''
    for ident, patient, status, tone, tries, updated, error in QUEUE_ROWS:
        exhausted = tries == '5'
        tries_html = (
            f'<span style="color: {t["danger"]}; font-weight: 600">{tries} из 5</span>'
            if exhausted else f'{tries} из 5'
        )
        rows += (
            f'<div style="display: contents">'
            f'<div style="padding: 9px 10px; border-top: 1px solid {t["border"]}; '
            f'font-family: Consolas, monospace">{ident}</div>'
            f'<div style="padding: 9px 10px; border-top: 1px solid {t["border"]}">{patient}</div>'
            f'<div style="padding: 9px 10px; border-top: 1px solid {t["border"]}">{badge(t, status, tone)}</div>'
            f'<div style="padding: 9px 10px; border-top: 1px solid {t["border"]}">{tries_html}</div>'
            f'<div style="padding: 9px 10px; border-top: 1px solid {t["border"]}; '
            f'color: {t["muted"]}">{updated}</div>'
            f'<div style="padding: 9px 10px; border-top: 1px solid {t["border"]}; '
            f'color: {t["muted"]}; overflow: hidden; text-overflow: ellipsis; '
            f'white-space: nowrap">{error}</div>'
            f'</div>'
        )

    table = (
        f'<div style="background: {t["surface"]}; border: 1px solid {t["border"]}; '
        f'border-radius: 5px; overflow: hidden">'
        f'<div style="display: grid; grid-template-columns: {" ".join(widths)}; '
        f'background: {t["surface_alt"]}">{header_row}</div>'
        f'<div style="display: grid; grid-template-columns: {" ".join(widths)}">{rows}</div>'
        f'</div>'
    )

    toolbar = (
        f'<div style="display: flex; align-items: center; gap: 8px">'
        f'{select_input(t, "Все, кроме отправленных", 232)}'
        f'<div style="display: flex; align-items: center; gap: 8px; width: 260px; '
        f'padding: 5px 8px; background: {t["surface"]}; border: 1px solid {t["border_strong"]}; '
        f'border-radius: 3px">{icon("search", t["muted"])}'
        f'<span style="color: {t["muted"]}">Поиск по пациенту или записи</span></div>'
        f'<span style="margin-left: auto"></span>'
        f'{button(t, "Дослать выбранные", "normal")}'
        f'{button(t, "Дослать всё", "normal")}'
        f'{button(t, "Выгрузить CSV", "normal", "download")}'
        f'{button(t, "Удалить", "danger", "trash")}'
        f'</div>'
    )

    notice = plate(t, 'warning',
                   'Служба держит очередь в памяти и перезаписывает файл целиком. Правки, сделанные '
                   'сейчас, она может затереть при следующем цикле — для массовых операций сначала '
                   'остановите её на странице «Обзор».',
                   'Служба сейчас работает')

    metrics_row = (
        f'<div style="display: flex; gap: 12px">'
        f'{metric(t, "Всего в очереди", "47", "из них 12 неотправляемых", "warning")}'
        f'{metric(t, "Ожидают отправки", "35", "попытки ещё остались")}'
        f'{metric(t, "Попытки исчерпаны", "12", "нужно дослать вручную", "danger")}'
        f'</div>'
    )

    content = notice + metrics_row + toolbar + table

    return shell(
        t,
        nav(t, 'queue'),
        header(t),
        content,
        footer(t, 'Показаны 7 записей из 47 · queue.json обновлён 09.09.2026 10:41:52',
               button(t, 'Обновить', 'normal', 'refresh')),
    )


# ----------------------------------------------------------------------
# Библиотека элементов
# ----------------------------------------------------------------------

def section(t, title, body, note=''):
    note_html = (
        f'<div style="font-size: 12px; color: {t["muted"]}; margin-bottom: 10px">{note}</div>'
        if note else ''
    )
    return (
        f'<div style="display: flex; flex-direction: column">'
        f'<div style="font-size: 11px; font-weight: 600; letter-spacing: .04em; '
        f'color: {t["muted"]}; margin-bottom: 8px">{title.upper()}</div>'
        f'{note_html}{body}</div>'
    )


def nav_sample(t, title, selected=False, dirty=False):
    colour = t['accent'] if selected else t['text']
    background = t['accent_soft'] if selected else t['surface']
    bar = t['accent'] if selected else 'transparent'
    mark = (
        f'<span style="margin-left: auto; width: 7px; height: 7px; border-radius: 50%; '
        f'background: {t["warning"]}"></span>' if dirty else ''
    )
    return (
        f'<div style="display: flex; align-items: center; gap: 10px; padding: 7px 16px; '
        f'width: {NAV_W}px; background: {background}; color: {colour}; '
        f'font-weight: {"600" if selected else "400"}; box-shadow: inset 3px 0 0 {bar}; '
        f'border: 1px solid {t["border"]}">'
        f'{icon("plug", colour)}<span>{title}</span>{mark}</div>'
    )


def library(t):
    row = 'display: flex; align-items: center; gap: 10px; flex-wrap: wrap'

    navigation = section(t, 'Пункты бокового меню',
        f'<div style="display: flex; flex-direction: column; gap: 6px">'
        f'{nav_sample(t, "Подключения")}'
        f'{nav_sample(t, "Подключения", selected=True)}'
        f'{nav_sample(t, "Подключения", dirty=True)}</div>',
        'обычный · выбранный · с несохранёнными правками')

    buttons = section(t, 'Кнопки',
        f'<div style="{row}">'
        f'{button(t, "Сохранить настройки", "primary")}'
        f'{button(t, "Отменить изменения", "normal")}'
        f'{button(t, "Обновить", "normal", "refresh")}'
        f'{button(t, "Показать", "quiet")}'
        f'{button(t, "Удалить", "danger", "trash")}</div>')

    inputs = section(t, 'Поля ввода',
        f'<div style="display: flex; flex-direction: column; gap: 8px">'
        f'<div style="{row}">{text_input(t, "SQL-01\\IDENT", 240)}'
        f'{number_input(t, "1433")}{select_input(t, "Как в системе", 200)}</div>'
        f'<div style="{row}">{text_input(t, "•••••••••", 240)}'
        f'{text_input(t, "UF_CRM_… или пусто", 240, placeholder=True)}</div></div>',
        'ширина по смыслу значения: адрес — широко, порт и таймаут — узко')

    states = section(t, 'Значки состояния',
        f'<div style="{row}">'
        f'<span style="display: inline-flex; align-items: center; gap: 6px">{dot(t["success"])}Работает</span>'
        f'<span style="display: inline-flex; align-items: center; gap: 6px">{dot(t["warning"])}Требует внимания</span>'
        f'<span style="display: inline-flex; align-items: center; gap: 6px">{dot(t["danger"])}Остановлена</span>'
        f'<span style="display: inline-flex; align-items: center; gap: 6px">{dot(t["border_strong"])}Неизвестно</span>'
        f'{badge(t, "Отправлено", "success")}{badge(t, "Ожидает", "warning")}'
        f'{badge(t, "Ошибка", "danger")}{badge(t, "Черновик")}</div>')

    plates = section(t, 'Плашки сообщений',
        f'<div style="display: flex; flex-direction: column; gap: 8px">'
        f'{plate(t, "success", "Настройки записаны, резервная копия config.ini.20260909-134206.bak.")}'
        f'{plate(t, "warning", "Служба читает конфигурацию при запуске — перезапустите её, чтобы правки вступили в силу.")}'
        f'{plate(t, "danger", "Файл config.ini изменился на диске после того, как приложение его открыло.")}'
        f'</div>')

    tiles = section(t, 'Плитка показателя',
        f'<div style="display: flex; gap: 12px">'
        f'{metric(t, "Отставание синхронизации", "4 минуты", "данные обработаны до 13:42", "success")}'
        f'{metric(t, "В очереди", "47 записей", "12 с исчерпанными попытками", "warning")}'
        f'</div>')

    left = (
        f'<div style="display: flex; flex-direction: column; gap: 20px; width: 470px">'
        f'{navigation}{buttons}{inputs}</div>'
    )
    right = (
        f'<div style="display: flex; flex-direction: column; gap: 20px; flex: 1">'
        f'{states}{plates}{tiles}</div>'
    )

    return (
        f'<div style="width: {W}px; height: {H}px; background: {t["background"]}; '
        f'color: {t["text"]}; font-family: {FONT}; font-size: 13px; padding: 24px; '
        f'box-sizing: border-box; overflow: hidden">'
        f'<div style="font-size: 17px; font-weight: 600; margin-bottom: 4px">Элементы интерфейса</div>'
        f'<div style="font-size: 12px; color: {t["muted"]}; margin-bottom: 20px">'
        f'Цвета и размеры — из gui/theme/tokens.py, без округлений</div>'
        f'<div style="display: flex; gap: 28px">{left}{right}</div></div>'
    )


# ----------------------------------------------------------------------

if __name__ == '__main__':
    print('Артборды:')
    write('Main.dc.html', overview(LIGHT, alarm=False))
    write('ObzorTrevoga.dc.html', overview(LIGHT, alarm=True))
    write('Podklyucheniya.dc.html', connections(LIGHT))
    write('Ochered.dc.html', queue(LIGHT))
    write('ObzorTemnaya.dc.html', overview(DARK, alarm=False))
    write('Elementy.dc.html', library(LIGHT))
