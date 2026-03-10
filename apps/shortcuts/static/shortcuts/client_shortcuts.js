(function () {
  const configUrl = document.body?.dataset?.shortcutConfigUrl;
  const executeBase = document.body?.dataset?.shortcutExecuteBase;
  if (!configUrl || !executeBase) {
    return;
  }

  const normalize = (parts) => parts.filter(Boolean).join('+').toUpperCase();

  const comboFromEvent = (event) => {
    const parts = [];
    if (event.ctrlKey) parts.push('CTRL');
    if (event.altKey) parts.push('ALT');
    if (event.shiftKey) parts.push('SHIFT');
    if (event.metaKey) parts.push('META');
    const key = (event.key || '').toUpperCase();
    if (!['CONTROL', 'SHIFT', 'ALT', 'META'].includes(key)) {
      parts.push(key);
    }
    return normalize(parts);
  };

  const csrfToken = () => {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) return input.value;
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  };

  const readClipboard = async () => {
    if (navigator.clipboard && navigator.clipboard.readText) {
      try {
        return await navigator.clipboard.readText();
      } catch (_error) {
        return '';
      }
    }
    return '';
  };

  const writeClipboard = async (value) => {
    if (!value) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(value);
    }
  };

  const typeToActiveElement = (text) => {
    if (!text) return;
    const el = document.activeElement;
    if (!el || !(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) {
      return;
    }
    const start = typeof el.selectionStart === 'number' ? el.selectionStart : el.value.length;
    const end = typeof el.selectionEnd === 'number' ? el.selectionEnd : el.value.length;
    const next = el.value.slice(0, start) + text + el.value.slice(end);
    el.value = next;
    el.selectionStart = el.selectionEnd = start + text.length;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };

  const fetchConfig = async () => {
    const response = await fetch(configUrl, { credentials: 'same-origin' });
    if (!response.ok) return [];
    const data = await response.json();
    if (!data.enabled) return [];
    return data.shortcuts || [];
  };

  const buildExecuteUrl = (id) => `${executeBase}${id}/`;

  const runShortcut = async (shortcut) => {
    const clipboard = await readClipboard();
    const response = await fetch(buildExecuteUrl(shortcut.id), {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken(),
      },
      body: JSON.stringify({ clipboard }),
    });
    if (!response.ok) return;
    const data = await response.json();
    if (data.clipboard_output) {
      await writeClipboard(data.clipboard_output);
    }
    if (data.keyboard_output) {
      typeToActiveElement(data.keyboard_output);
    }
  };

  fetchConfig().then((shortcuts) => {
    if (!shortcuts.length) return;
    const map = new Map(shortcuts.map((entry) => [String(entry.key_combo || '').toUpperCase(), entry]));
    window.addEventListener('keydown', (event) => {
      const combo = comboFromEvent(event);
      const shortcut = map.get(combo);
      if (!shortcut) return;
      event.preventDefault();
      runShortcut(shortcut).catch(console.error);
    });
  }).catch(console.error);
})();
