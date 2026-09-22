"""
Сборка иконки приложения из описания, а не из картинки.

Иконка нужна в семи размерах, и мелкие — не уменьшенные копии крупных:
шестерня с восемью зубьями и стрелкой на 16 пикселях превращается в пятно.
Поэтому здесь два рисунка: подробный для крупных размеров и упрощённый
для мелких, а `.ico` собирается из обоих.

Запуск:  python tools/make_icon.py
Итог:    gui/assets/app.ico, gui/assets/app.svg (крупный вариант — для документации)

Нужен rsvg-convert (пакет librsvg2-bin).
"""

import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'gui' / 'assets'

#: фирменный синий — тот же, что accent в тёмной теме приложения
BLUE = '#2C5AA0'
BLUE_DEEP = '#1E4076'
WHITE = '#FFFFFF'

#: размеры, которые Windows берёт из .ico
SIZES = (16, 24, 32, 48, 64, 128, 256)

#: с какого размера рисуем подробный вариант
DETAILED_FROM = 48

CANVAS = 256


def gear_path(cx: float, cy: float, outer: float, inner: float,
              teeth: int, hole: float) -> str:
    """
    Контур шестерни: зубья многоугольником, отверстие — вторым контуром.

    Многоугольник вместо дуг выбран сознательно: на 16 пикселях сглаженная
    дуга даёт серую кашу, а прямые грани остаются читаемыми.
    """
    step = math.pi * 2 / teeth
    # доля шага, которую занимает зуб; остальное — впадина
    tooth = step * 0.42
    slope = step * 0.10

    points = []
    for index in range(teeth):
        base = index * step

        for angle, radius in (
            (base - tooth / 2 - slope, inner),
            (base - tooth / 2, outer),
            (base + tooth / 2, outer),
            (base + tooth / 2 + slope, inner),
        ):
            points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))

    body = 'M ' + ' L '.join(f'{x:.2f},{y:.2f}' for x, y in points) + ' Z'

    # отверстие — окружность двумя дугами, направление обратное (fill-rule=evenodd)
    hole_path = (
        f'M {cx - hole:.2f},{cy:.2f} '
        f'A {hole:.2f},{hole:.2f} 0 1,0 {cx + hole:.2f},{cy:.2f} '
        f'A {hole:.2f},{hole:.2f} 0 1,0 {cx - hole:.2f},{cy:.2f} Z'
    )

    return f'{body} {hole_path}'


def svg(detailed: bool) -> str:
    """Разметка иконки. `detailed` — вариант для крупных размеров."""
    radius = 56 if CANVAS == 256 else CANVAS * 0.22

    if detailed:
        gear = gear_path(cx=104, cy=128, outer=78, inner=60, teeth=8, hole=26)
        arrow = '''
  <g transform="translate(150,128)">
    <path d="M -6,-13 L 34,-13 L 34,-34 L 74,0 L 34,34 L 34,13 L -6,13 Z"
          fill="{white}" stroke="{blue}" stroke-width="12" stroke-linejoin="round"/>
    <path d="M -6,-13 L 34,-13 L 34,-34 L 74,0 L 34,34 L 34,13 L -6,13 Z"
          fill="{white}"/>
  </g>'''.format(white=WHITE, blue=BLUE)
    else:
        # Мельче: зубьев меньше и они толще, стрелки нет — на 16 пикселях
        # два знака в одном поле не различаются
        gear = gear_path(cx=128, cy=128, outer=100, inner=72, teeth=6, hole=32)
        arrow = ''

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS}" height="{CANVAS}"
     viewBox="0 0 {CANVAS} {CANVAS}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{BLUE}"/>
      <stop offset="1" stop-color="{BLUE_DEEP}"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="{CANVAS}" height="{CANVAS}" rx="{radius}" fill="url(#bg)"/>
  <path d="{gear}" fill="{WHITE}" fill-rule="evenodd"/>{arrow}
</svg>
'''


def render(markup: str, size: int, target: Path) -> None:
    with tempfile.NamedTemporaryFile('w', suffix='.svg', encoding='utf-8', delete=False) as f:
        f.write(markup)
        source = Path(f.name)

    try:
        subprocess.run(
            ['rsvg-convert', '-w', str(size), '-h', str(size), '-o', str(target), str(source)],
            check=True, capture_output=True
        )
    finally:
        source.unlink(missing_ok=True)


def write_ico(frames: List[Tuple[int, bytes]], target: Path) -> None:
    """
    Собирает .ico из готовых png.

    Формат складывается вручную намеренно: Pillow при сохранении .ico берёт
    один кадр и масштабирует его под остальные размеры, увеличивать при этом
    отказывается — из семи размеров в файл попадал только самый мелкий.
    Здесь каждый размер отрисован отдельно и кладётся как есть.

    Windows начиная с Vista читает png внутри .ico во всех размерах.
    """
    count = len(frames)
    header = struct.pack('<HHH', 0, 1, count)

    offset = len(header) + count * 16
    directory = b''
    payload = b''

    for size, data in frames:
        directory += struct.pack(
            '<BBBBHHII',
            0 if size >= 256 else size,   # 0 означает 256
            0 if size >= 256 else size,
            0,                            # палитра не используется
            0,                            # зарезервировано
            1,                            # плоскостей
            32,                           # бит на пиксель
            len(data),
            offset
        )
        payload += data
        offset += len(data)

    target.write_bytes(header + directory + payload)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)

    detailed = svg(detailed=True)
    simple = svg(detailed=False)

    (ASSETS / 'app.svg').write_text(detailed, encoding='utf-8')

    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for size in SIZES:
            png = Path(tmp) / f'{size}.png'
            render(detailed if size >= DETAILED_FROM else simple, size, png)
            frames.append((size, png.read_bytes()))

        write_ico(frames, ASSETS / 'app.ico')

        # Отдельный png — для README и страниц документации
        render(detailed, 256, ASSETS / 'app.png')

    print(f'Готово: {ASSETS / "app.ico"} ({", ".join(str(s) for s in SIZES)})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
