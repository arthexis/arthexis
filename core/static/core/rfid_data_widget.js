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
      const hasValue = index < bytes.length;
      if (!hasValue) {
        cell.textContent = "--";
        return;
      }
      const byte = normalizeByte(bytes[index]);
      cell.textContent = byte.toString(16).toUpperCase().padStart(2, "0");
    });
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

    const textInputs = widgetEl.querySelectorAll(".rfid-data-widget__text-input");

    function refreshFromState() {
      textInputs.forEach((input) => {
        const blockNumber = Number.parseInt(input.dataset.block || "", 10);
        if (Number.isNaN(blockNumber)) {
          input.value = "";
          return;
        }
        const info = state.map.get(blockNumber);
        if (!info) {
          input.value = "";
          updateHexRow(blockRows.get(blockNumber), []);
          return;
        }
        input.value = formatBytesToText(info.entry.data);
        updateHexRow(blockRows.get(blockNumber), info.entry.data);
      });
    }

    textInputs.forEach((input) => {
      const blockNumber = Number.parseInt(input.dataset.block || "", 10);
      if (Number.isNaN(blockNumber)) {
        return;
      }
      const info = state.map.get(blockNumber);
      if (info) {
        input.value = formatBytesToText(info.entry.data);
        updateHexRow(blockRows.get(blockNumber), info.entry.data);
      }

      input.addEventListener("input", () => {
        const current = state.map.get(blockNumber);
        if (!current) {
          return;
        }
        const existingBytes = current.entry.data.slice();
        const characters = Array.from(input.value).slice(0, 16);
        while (characters.length < 16) {
          characters.push("·");
        }
        const updatedBytes = characters.map((character, index) => {
          if (character === "·") {
            return existingBytes[index] ?? 0;
          }
          const codePoint = character.codePointAt(0);
          if (typeof codePoint !== "number") {
            return existingBytes[index] ?? 0;
          }
          return normalizeByte(codePoint);
        });
        current.entry.data = updatedBytes;
        state.entries[current.index] = current.entry;
        input.value = formatBytesToText(updatedBytes);
        updateHexRow(blockRows.get(blockNumber), updatedBytes);
        syncRawInput(rawInput, state);
      });
    });

    function handleRawInputChange() {
      state = parseRawEntries(rawInput.value);
      refreshFromState();
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
