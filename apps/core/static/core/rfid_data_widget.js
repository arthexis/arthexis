(function () {
  "use strict";

  function normalizeByte(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return Math.min(Math.max(Math.trunc(value), 0), 255);
    }
    const parsed = Number.parseInt(value, 10);
    if (Number.isNaN(parsed)) {
      return 0;
    }
    return Math.min(Math.max(parsed, 0), 255);
  }

  function formatBytesToText(bytes) {
    const chars = [];
    for (let index = 0; index < 16; index += 1) {
      const byte = normalizeByte(bytes[index]);
      if (byte >= 32 && byte <= 126) {
        chars.push(String.fromCharCode(byte));
      } else {
        chars.push("·");
      }
    }
    return chars.join("");
  }

  function updateHexRow(row, bytes) {
    if (!row) {
      return;
    }
    const cells = row.querySelectorAll("td[data-byte-index]");
    cells.forEach((cell, index) => {
      const button = cell.querySelector("[data-edit-block]");
      if (!button) {
        return;
      }
      if (!Array.isArray(bytes) || index >= bytes.length) {
        button.textContent = "--";
        return;
      }
      const byte = normalizeByte(bytes[index]);
      button.textContent = byte.toString(16).toUpperCase().padStart(2, "0");
    });
  }

  function formatBytesToHex(bytes) {
    return Array.from({ length: 16 }, (_, index) => {
      const byte = normalizeByte(bytes[index]);
      return byte.toString(16).toUpperCase().padStart(2, "0");
    }).join(" ");
  }

  function parseHexInput(value) {
    const matches = (value || "").match(/[0-9a-fA-F]{2}/g) || [];
    const bytes = [];
    for (let index = 0; index < 16; index += 1) {
      if (index < matches.length) {
        bytes.push(normalizeByte(Number.parseInt(matches[index], 16)));
      } else {
        bytes.push(0);
      }
    }
    return bytes;
  }

  function parseTextInput(value) {
    const characters = Array.from(value || "").slice(0, 16);
    const bytes = [];
    for (let index = 0; index < 16; index += 1) {
      if (index < characters.length) {
        const codePoint = characters[index].codePointAt(0);
        if (typeof codePoint === "number") {
          bytes.push(normalizeByte(codePoint));
        } else {
          bytes.push(0);
        }
      } else {
        bytes.push(0);
      }
    }
    return bytes;
  }

  function parseRawEntries(rawValue) {
    let parsed;
    try {
      parsed = JSON.parse(rawValue || "[]");
    } catch (error) {
      parsed = [];
    }
    if (!Array.isArray(parsed)) {
      parsed = [];
    }

    const entries = parsed.map((entry) => {
      if (entry && typeof entry === "object") {
        return { ...entry };
      }
      return {};
    });

    const map = new Map();
    entries.forEach((entry, index) => {
      if (typeof entry.block !== "number") {
        return;
      }
      const data = Array.isArray(entry.data) ? entry.data : [];
      const normalized = [];
      for (let position = 0; position < 16; position += 1) {
        const value = position < data.length ? normalizeByte(data[position]) : 0;
        normalized.push(value);
      }
      entry.data = normalized;
      map.set(entry.block, { entry, index });
    });
    return { entries, map };
  }

  function syncRawInput(rawInput, state) {
    rawInput.value = JSON.stringify(state.entries, null, 2);
  }

  function initWidget(widgetEl) {
    const rawInput = widgetEl.querySelector(".rfid-data-widget__input");
    if (!rawInput) {
      return;
    }

    let state = parseRawEntries(rawInput.value);

    const blockRows = new Map();
    widgetEl.querySelectorAll(".rfid-data-widget__block[data-block]").forEach((row) => {
      const blockNumber = Number.parseInt(row.dataset.block || "", 10);
      if (!Number.isNaN(blockNumber)) {
        blockRows.set(blockNumber, row);
      }
    });

    const textButtons = new Map();
    widgetEl.querySelectorAll(".rfid-data-widget__value--text[data-edit-block]").forEach((button) => {
      const blockNumber = Number.parseInt(button.dataset.editBlock || "", 10);
      if (!Number.isNaN(blockNumber)) {
        textButtons.set(blockNumber, button);
      }
    });

    const keyButtons = new Map();
    widgetEl.querySelectorAll(".rfid-data-widget__key [data-edit-block]").forEach((button) => {
      const blockNumber = Number.parseInt(button.dataset.editBlock || "", 10);
      if (!Number.isNaN(blockNumber)) {
        keyButtons.set(blockNumber, button);
      }
    });

    const modalEl = widgetEl.querySelector("[data-rfid-modal]");
    const dialogEl = modalEl ? modalEl.querySelector(".rfid-data-widget__modal-dialog") : null;
    const hexField = modalEl ? modalEl.querySelector("[data-rfid-modal-hex]") : null;
    const textField = modalEl ? modalEl.querySelector("[data-rfid-modal-text]") : null;
    const keyField = modalEl ? modalEl.querySelector("[data-rfid-modal-key]") : null;
    const keyContainer = modalEl ? modalEl.querySelector("[data-rfid-modal-key-container]") : null;
    const saveButton = modalEl ? modalEl.querySelector("[data-rfid-modal-save]") : null;
    const dismissButtons = modalEl ? modalEl.querySelectorAll("[data-rfid-modal-dismiss]") : [];
    const titleBlockEl = modalEl ? modalEl.querySelector("[data-rfid-modal-block]") : null;
    const metaEl = modalEl ? modalEl.querySelector("[data-rfid-modal-meta]") : null;

    const sectorLabel = modalEl ? modalEl.dataset.sectorLabel || "Sector" : "Sector";
    const blockLabel = modalEl ? modalEl.dataset.blockLabel || "Block" : "Block";
    const offsetLabel = modalEl ? modalEl.dataset.offsetLabel || "Offset" : "Offset";

    let activeBlock = null;
    let draftBytes = new Array(16).fill(0);
    let draftKey = "";
    let restoreFocus = null;

    if (dialogEl && !dialogEl.hasAttribute("tabindex")) {
      dialogEl.setAttribute("tabindex", "-1");
    }

    function ensureBlock(blockNumber) {
      let info = state.map.get(blockNumber);
      if (info) {
        return info;
      }
      const entry = {
        block: blockNumber,
        data: Array.from({ length: 16 }, () => 0),
      };
      state.entries.push(entry);
      info = { entry, index: state.entries.length - 1 };
      state.map.set(blockNumber, info);
      return info;
    }

    function shouldShowKey(blockNumber) {
      const row = blockRows.get(blockNumber);
      return Boolean(row && row.classList.contains("rfid-data-widget__block--trailer"));
    }

    function updateKeyDisplay(blockNumber) {
      const button = keyButtons.get(blockNumber);
      if (!button) {
        return;
      }
      const info = state.map.get(blockNumber);
      if (info && typeof info.entry.key === "string" && info.entry.key.trim() !== "") {
        button.textContent = info.entry.key;
      } else {
        button.textContent = "—";
      }
    }

    function updateTextDisplay(blockNumber) {
      const button = textButtons.get(blockNumber);
      if (!button) {
        return;
      }
      const info = state.map.get(blockNumber);
      if (!info) {
        button.textContent = "················";
        return;
      }
      button.textContent = formatBytesToText(info.entry.data);
    }

    function updateBlockDisplay(blockNumber) {
      const info = state.map.get(blockNumber);
      const bytes = info ? info.entry.data : [];
      updateHexRow(blockRows.get(blockNumber), bytes);
      updateTextDisplay(blockNumber);
      updateKeyDisplay(blockNumber);
    }

    function refreshFromState() {
      blockRows.forEach((_, blockNumber) => {
        updateBlockDisplay(blockNumber);
      });
    }

    function openEditor(blockNumber, options) {
      if (!modalEl || !hexField || !textField) {
        return;
      }
      const { focusKey = false } = options || {};
      const info = ensureBlock(blockNumber);
      activeBlock = blockNumber;
      draftBytes = Array.isArray(info.entry.data) ? info.entry.data.slice(0, 16) : new Array(16).fill(0);
      draftKey = typeof info.entry.key === "string" ? info.entry.key : "";
      const sector = Math.trunc(blockNumber / 4);
      const offset = blockNumber % 4;

      hexField.value = formatBytesToHex(draftBytes);
      textField.value = formatBytesToText(draftBytes);

      if (keyContainer && keyField) {
        const showKey = shouldShowKey(blockNumber);
        keyContainer.hidden = !showKey;
        if (showKey) {
          keyField.value = draftKey;
        } else {
          keyField.value = "";
        }
      }

      if (titleBlockEl) {
        titleBlockEl.textContent = String(blockNumber);
      }
      if (metaEl) {
        metaEl.textContent = `${sectorLabel} ${sector} · ${blockLabel} ${blockNumber} (${offsetLabel} ${offset})`;
      }

      modalEl.hidden = false;

      const initialFocus =
        focusKey && keyField && keyContainer && !keyContainer.hidden ? keyField : hexField;

      window.requestAnimationFrame(() => {
        const target = initialFocus;
        if (target && typeof target.focus === "function") {
          target.focus();
          if (typeof target.select === "function") {
            target.select();
          }
        }
      });
    }

    function closeEditor() {
      if (!modalEl) {
        return;
      }
      modalEl.hidden = true;
      activeBlock = null;
      draftBytes = new Array(16).fill(0);
      draftKey = "";
      if (restoreFocus && typeof restoreFocus.focus === "function") {
        restoreFocus.focus();
      }
      restoreFocus = null;
    }

    if (hexField) {
      hexField.addEventListener("input", () => {
        if (activeBlock === null) {
          return;
        }
        draftBytes = parseHexInput(hexField.value);
        if (textField) {
          textField.value = formatBytesToText(draftBytes);
        }
      });
    }

    if (textField) {
      textField.addEventListener("input", () => {
        if (activeBlock === null) {
          return;
        }
        draftBytes = parseTextInput(textField.value);
        if (hexField) {
          hexField.value = formatBytesToHex(draftBytes);
        }
      });
    }

    if (keyField) {
      keyField.addEventListener("input", () => {
        if (activeBlock === null) {
          return;
        }
        draftKey = keyField.value;
      });
    }

    if (saveButton) {
      saveButton.addEventListener("click", () => {
        if (activeBlock === null) {
          return;
        }
        const info = ensureBlock(activeBlock);
        info.entry.data = draftBytes.slice();
        if (keyField && keyContainer && !keyContainer.hidden) {
          const trimmed = draftKey.trim();
          if (trimmed) {
            info.entry.key = trimmed;
          } else {
            delete info.entry.key;
          }
        } else {
          delete info.entry.key;
        }
        state.entries[info.index] = info.entry;
        state.map.set(activeBlock, info);
        syncRawInput(rawInput, state);
        updateBlockDisplay(activeBlock);
        closeEditor();
      });
    }

    dismissButtons.forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        closeEditor();
      });
    });

    if (modalEl) {
      modalEl.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && activeBlock !== null) {
          event.preventDefault();
          closeEditor();
        }
      });
    }

    widgetEl.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-edit-block]");
      if (!trigger || !widgetEl.contains(trigger)) {
        return;
      }
      event.preventDefault();
      const blockNumber = Number.parseInt(trigger.dataset.editBlock || "", 10);
      if (Number.isNaN(blockNumber)) {
        return;
      }
      restoreFocus = trigger;
      const focusKey = trigger.dataset.editKey === "true";
      openEditor(blockNumber, { focusKey });
    });

    function handleRawInputChange() {
      state = parseRawEntries(rawInput.value);
      refreshFromState();
      if (activeBlock !== null) {
        closeEditor();
      }
    }

    rawInput.addEventListener("change", handleRawInputChange);
    rawInput.addEventListener("input", handleRawInputChange);

    refreshFromState();
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".rfid-data-widget").forEach((widgetEl) => {
      initWidget(widgetEl);
    });
  });
})();
