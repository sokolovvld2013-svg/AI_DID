"""Скрытие признаков автоматизации браузера (stealth).

Подключается через ``BrowserContext.add_init_script`` и выполняется в каждом
новом документе до отрисовки страницы.

Осторожно с подменами: КАД активно проверяет ``navigator.plugins`` и
``iframe.contentWindow``, и грубая подмена (например, объект вместо
``PluginArray``) ломает его JS с ошибкой ``unreachable`` — после этого кнопка
«Найти» молча не срабатывает. Поэтому каждый патч либо срабатывает только при
реальном отсутствии свойства, либо сохраняет нативную семантику.
"""

from __future__ import annotations

STEALTH_SCRIPT = r"""
(() => {
  const patch = (obj, prop, value) => {
    try {
      Object.defineProperty(obj, prop, { get: () => value, configurable: true });
    } catch (e) { /* свойство нельзя переопределить — не критично */ }
  };

  // 1. navigator.webdriver === true — главный признак автоматизации.
  patch(Navigator.prototype, 'webdriver', undefined);
  delete navigator.__proto__.webdriver;

  // 2. Наборы шрифтов и языков, реально присутствующие у живого Chrome.
  patch(navigator, 'languages', ['ru-RU', 'ru', 'en-US', 'en']);
  patch(navigator, 'platform', 'Win32');
  patch(navigator, 'hardwareConcurrency', 8);
  patch(navigator, 'deviceMemory', 8);

  // 3. window.chrome присутствует только в «настоящем» Chrome.
  if (!window.chrome) {
    window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {} };
  }

  // 4. Плагины: в headless их нет, что выглядит подозрительно.
  //    Патчим ТОЛЬКО когда их действительно нет. В видимом Chrome PDF-плагины
  //    есть всегда, и подмена на объект ломает код КАД: тот проверяет
  //    navigator.plugins и падает с «unreachable», из-за чего «Найти» не работает.
  try {
    if (!navigator.plugins || navigator.plugins.length === 0) {
      const fake = {
        0: { type: 'application/pdf', name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
        1: { type: 'application/pdf', name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
        2: { type: 'application/pdf', name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer' },
        3: { type: 'application/pdf', name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer' },
        4: { type: 'application/pdf', name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer' },
        length: 5,
        item: function (i) { return this[i] || null; },
        namedItem: function (n) { return this[n] || null; },
        refresh: function () {}
      };
      // Наследование от PluginArray.prototype нужно проверкам вида
      // ``navigator.plugins instanceof PluginArray`` — без него они не проходят.
      if (typeof PluginArray === 'function') {
        Object.setPrototypeOf(fake, PluginArray.prototype);
      }
      patch(navigator, 'plugins', fake);
    }
  } catch (e) { /* не критично */ }

  // 5. WebGL — без маскировки попадаем в пустой "SwiftShader".
  const patchWebGL = (proto) => {
    if (!proto) return;
    const info = {
      vendor: 'Google Inc. (Intel)',
      renderer: 'ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics OpenGL Engine, D3D11)'
    };
    patch(proto, 'getParameter', new Proxy(proto.getParameter, {
      apply(target, thisArg, args) {
        if (args[0] === 0x1F00) return info.vendor;
        if (args[0] === 0x1F01) return info.renderer;
        return Reflect.apply(target, thisArg, args);
      }
    }));
  };
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    patchWebGL(gl && Object.getPrototypeOf(gl));
  } catch (e) { /* WebGL недоступен */ }

  // 6. Permissions API: headless отдаёт "denied" на запрос уведомлений.
  //    Обязательно сохраняем this — иначе нативный метод падает с
  //    «Illegal invocation» и ломает проверки на стороне сайта.
  try {
    const originalQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (parameters) =>
      parameters && parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission, onchange: null })
        : originalQuery(parameters);
  } catch (e) { /* нет API */ }

  // 7. iframe.contentWindow — классическая проверка на headless.
  //    Патчим только когда обращения действительно нет, и не подменяем
  //    самство: ранний код КАД ходит в iframe.contentWindow.document.
  try {
    const originalDescriptor =
      Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    if (originalDescriptor && originalDescriptor.get) {
      const originalGet = originalDescriptor.get;
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        configurable: true,
        get() {
          const win = originalGet.call(this);
          if (win) return win;
          // Современный Chrome всегда отдаёт Window; undefined означает, что
          // окно ещё не создано — ничего подменять не нужно.
          return undefined;
        }
      });
    }
  } catch (e) { /* не критично */ }

  // 8. Ложные границы screen/dimension, нехарактерные для headless.
  patch(screen, 'availWidth', window.innerWidth);
  patch(screen, 'availHeight', window.innerHeight - 40);
  patch(screen, 'width', window.innerWidth);
  patch(screen, 'height', window.innerHeight);
})();
"""
