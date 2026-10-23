(() => {
  'use strict';
  const form = document.getElementById('byok-form');
  const fields = ['gemini-key', 'notion-token', 'line-token', 'line-user'].map(id => document.getElementById(id));
  const [gemini, notion, lineToken, lineUser] = fields;
  const error = document.getElementById('form-error');
  const output = document.getElementById('output');
  const placeholder = document.getElementById('output-placeholder');
  const preview = document.getElementById('json-preview');
  const status = document.getElementById('output-status');
  const reveal = document.getElementById('reveal');
  let generated = '';
  let shown = false;

  function renderPreview() {
    preview.value = shown ? generated : JSON.stringify(
      Object.fromEntries(Object.keys(JSON.parse(generated)).map(name => [name, '••••••••'])), null, 2
    );
    reveal.textContent = shown ? '遮蔽完整 JSON' : '顯示完整 JSON';
    reveal.setAttribute('aria-pressed', String(shown));
  }

  function invalidate() {
    const hadResult = Boolean(generated);
    generated = '';
    shown = false;
    preview.value = '';
    output.hidden = true;
    placeholder.hidden = false;
    error.textContent = '';
    for (const field of fields) field.removeAttribute('aria-invalid');
    reveal.setAttribute('aria-pressed', 'false');
    status.textContent = hadResult ? '設定已修改，舊 JSON 已清除；請重新產生。' : '';
  }

  for (const field of fields) {
    field.addEventListener('input', invalidate);
    field.addEventListener('change', invalidate);
  }

  form.addEventListener('submit', event => {
    event.preventDefault();
    invalidate();
    for (const field of fields) field.value = field.value.trim();
    if (!form.reportValidity()) return;
    if (Boolean(lineToken.value) !== Boolean(lineUser.value)) {
      error.textContent = '使用 LINE 通知時，請一起填寫 Channel Access Token 與 User ID，或將兩欄一起清空。';
      const missing = lineToken.value ? lineUser : lineToken;
      missing.setAttribute('aria-invalid', 'true');
      missing.focus();
      return;
    }
    const data = {'X-Gemini-Api-Key': gemini.value, 'X-Notion-Token': notion.value};
    if (lineToken.value) {
      data['X-Line-Token'] = lineToken.value;
      data['X-Line-User-ID'] = lineUser.value;
    }
    generated = JSON.stringify(data);
    if (new TextEncoder().encode(generated).length > 8192) {
      generated = '';
      error.textContent = '設定內容太長，請檢查是否貼入金鑰以外的內容。';
      error.focus();
      return;
    }
    shown = false;
    renderPreview();
    output.hidden = false;
    placeholder.hidden = true;
    status.textContent = '設定 JSON 已產生。金鑰尚未經服務驗證，捷徑也尚未安裝。';
    preview.focus();
  });

  reveal.addEventListener('click', () => {
    if (!generated) return;
    shown = !shown;
    renderPreview();
  });

  document.getElementById('copy').addEventListener('click', async () => {
    if (!generated) return;
    const copied = generated;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(copied);
      if (generated === copied) status.textContent = '完整 JSON 已複製，包含未遮蔽的金鑰。請貼到你信任的捷徑。';
    } catch {
      if (generated !== copied) return;
      shown = true;
      renderPreview();
      preview.focus();
      preview.select();
      status.textContent = '無法自動複製，已顯示並選取完整 JSON。請使用系統的「複製」，貼上後清除本頁設定。';
    }
  });

  document.getElementById('clear-settings').addEventListener('click', () => {
    form.reset();
    invalidate();
    status.textContent = '本頁設定已清除；已複製到剪貼簿的內容請自行移除。';
    gemini.focus();
  });
  window.addEventListener('pagehide', () => {
    form.reset();
    invalidate();
  });
})();
