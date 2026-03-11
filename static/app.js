const form = document.getElementById('upload-form');
const statusEl = document.getElementById('status');
const workspace = document.getElementById('workspace');
const categoriesEl = document.getElementById('categories');
const previewEl = document.getElementById('preview');
const titleEl = document.getElementById('title');
const metaEl = document.getElementById('meta');
const searchEl = document.getElementById('search');

let currentData = null;

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  statusEl.textContent = 'Разбор архива...';

  try {
    const fd = new FormData(form);
    const response = await fetch('/analyze', { method: 'POST', body: fd });
    const data = await response.json();

    if (!response.ok) {
      statusEl.textContent = data.error || 'Ошибка анализа';
      if (data.jad) {
        statusEl.textContent += ` (${Object.entries(data.jad).map(([k, v]) => `${k}: ${v}`).join(' | ')})`;
      }
      return;
    }

    currentData = data;
    statusEl.textContent = `Готово: ${data.archive_name}, файлов: ${data.file_count}`;
    workspace.classList.remove('hidden');
    renderCategories();
  } catch (error) {
    statusEl.textContent = `Ошибка сети: ${error}`;
  }
});

searchEl.addEventListener('input', () => renderCategories(searchEl.value.trim().toLowerCase()));

function renderCategories(filter = '') {
  categoriesEl.innerHTML = '';
  previewEl.innerHTML = '';
  titleEl.textContent = 'Выберите файл';
  metaEl.textContent = '';

  for (const [category, files] of Object.entries(currentData.categories)) {
    const visible = files.filter((f) => f.path.toLowerCase().includes(filter));
    if (!visible.length) continue;

    const wrapper = document.createElement('details');
    wrapper.className = 'category';
    wrapper.open = true;

    const summary = document.createElement('summary');
    summary.textContent = `${category} (${visible.length})`;
    wrapper.appendChild(summary);

    visible.forEach((file) => {
      const item = document.createElement('button');
      item.className = 'file-item';
      item.type = 'button';
      item.textContent = file.path;
      item.onclick = () => selectFile(file, item);
      wrapper.appendChild(item);
    });

    categoriesEl.appendChild(wrapper);
  }
}

function clearSelectedFile() {
  document.querySelectorAll('.file-item.active').forEach((node) => node.classList.remove('active'));
}

function setButtonColor(btn, mode) {
  const palette = {
    gray: '#5f6578',
    orange: '#d9822b',
    blue: '#2d6cdf',
    green: '#2ea44f',
  };
  btn.style.background = palette[mode] || palette.gray;
}

async function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.left = '-9999px';
  document.body.appendChild(area);
  area.select();
  document.execCommand('copy');
  document.body.removeChild(area);
}

function formatHexData(uint8) {
  const lines = [];
  const pureHex = [];
  const asciiOnly = [];

  for (let i = 0; i < uint8.length; i += 16) {
    const chunk = Array.from(uint8.slice(i, i + 16));
    const hex = chunk.map((b) => b.toString(16).padStart(2, '0'));
    const ascii = chunk.map((b) => (b >= 32 && b <= 126 ? String.fromCharCode(b) : '.'));

    lines.push(`${i.toString(16).padStart(8, '0')}: ${hex.join(' ').padEnd(47, ' ')}  ${ascii.join('')}`);
    pureHex.push(...hex);
    asciiOnly.push(...ascii);
  }

  return {
    pretty: lines.join('\n'),
    hexOnly: pureHex.join(' '),
    asciiOnly: asciiOnly.join(''),
  };
}

async function selectFile(file, itemNode) {
  clearSelectedFile();
  if (itemNode) itemNode.classList.add('active');

  const session = currentData.session_id;
  const encodedPath = file.path.split('/').map(encodeURIComponent).join('/');
  const artifactUrl = `/artifact/${session}/${encodedPath}`;
  const downloadUrl = `/download/${session}/${encodedPath}`;
  const isClassFile = file.path.toLowerCase().endsWith('.class');

  titleEl.textContent = file.path;
  metaEl.innerHTML = `
    <div class="meta-grid">
      <span>Размер:</span><strong>${file.size} байт</strong>
      <span>SHA1:</span><strong class="sha-cell">${file.sha1}</strong>
      <span>MIME:</span><strong>${file.mime}</strong>
    </div>
    <a class="download-link" href="${downloadUrl}">Скачать файл</a>
  `;
  previewEl.innerHTML = '';

  if (file.mime.startsWith('image/')) {
    previewEl.innerHTML = `<img src="${artifactUrl}" alt="${file.path}">`;
    return;
  }

  if (file.mime.startsWith('audio/')) {
    previewEl.innerHTML = `<audio controls src="${artifactUrl}"></audio>`;
    return;
  }

  if (file.previewable && !isClassFile) {
    const text = await fetch(artifactUrl).then((r) => r.text());
    previewEl.innerHTML = `<pre class="code-box">${escapeHtml(text.slice(0, 50000))}</pre>`;
    return;
  }

  const bin = await fetch(artifactUrl).then((r) => r.arrayBuffer());
  const view = new Uint8Array(bin).slice(0, 2048);
  const hexData = formatHexData(view);

  previewEl.innerHTML = `
    <div class="actions-row">
      <button id="decompile-btn" class="action-btn" type="button">Декомпилировать .class</button>
      <button id="hex-cycle-btn" class="action-btn" type="button">Показать HEX</button>
    </div>
    <div id="preview-message"></div>
    <div id="result-panel"></div>
  `;

  const resultPanel = document.getElementById('result-panel');
  const messageEl = document.getElementById('preview-message');
  const decompileBtn = document.getElementById('decompile-btn');
  const hexBtn = document.getElementById('hex-cycle-btn');

  let javaSource = '';
  let javaLoaded = false;
  let hexState = 0; // 0 show, 1 copy hex, 2 copy ascii

  if (!isClassFile) {
    decompileBtn.style.display = 'none';
  } else {
    decompileBtn.style.display = 'inline-block';
    setButtonColor(decompileBtn, 'gray');

    decompileBtn.onclick = async () => {
      if (!javaLoaded) {
        decompileBtn.disabled = true;
        decompileBtn.textContent = 'Декомпиляция...';

        try {
          const response = await fetch(`/decompile/${session}/${encodedPath}`);
          const data = await response.json();

          if (data.java_source) {
            javaSource = data.java_source;
            javaLoaded = true;
            resultPanel.innerHTML = `<pre class="code-box">${escapeHtml(javaSource)}</pre>`;
            decompileBtn.textContent = '📋 Копировать Java код';
            setButtonColor(decompileBtn, 'green');
            return;
          }

          const err = data.error || 'Не удалось декомпилировать';
          resultPanel.innerHTML = `<p class="error">${escapeHtml(err)}</p>`;
          if (data.hex_preview) {
            resultPanel.innerHTML += `<pre class="hex-box">${escapeHtml(data.hex_preview)}</pre>`;
          }
          decompileBtn.textContent = 'Декомпилировать .class';
          setButtonColor(decompileBtn, 'gray');
        } catch (error) {
          resultPanel.innerHTML = `<p class="error">Сетевая ошибка: ${escapeHtml(String(error))}</p>`;
          decompileBtn.textContent = 'Декомпилировать .class';
          setButtonColor(decompileBtn, 'gray');
        } finally {
          decompileBtn.disabled = false;
        }
        return;
      }

      try {
        await copyToClipboard(javaSource);
        const prev = decompileBtn.textContent;
        decompileBtn.textContent = '✅ Скопировано!';
        setTimeout(() => {
          decompileBtn.textContent = prev || '📋 Копировать Java код';
        }, 1500);
      } catch (error) {
        resultPanel.innerHTML = `<p class="error">Ошибка копирования: ${escapeHtml(String(error))}</p>`;
      }
    };
  }

  setButtonColor(hexBtn, 'gray');
  hexBtn.onclick = async () => {
    try {
      if (hexState === 0) {
        resultPanel.innerHTML = `<pre class="hex-box">${escapeHtml(hexData.pretty)}</pre>`;
        hexBtn.textContent = '📋 Копировать HEX';
        setButtonColor(hexBtn, 'orange');
        hexState = 1;
        return;
      }

      if (hexState === 1) {
        await copyToClipboard(hexData.hexOnly);
        messageEl.innerHTML = '<p>✅ HEX скопирован!</p>';
        hexBtn.textContent = '📋 Копировать ASCII';
        setButtonColor(hexBtn, 'blue');
        hexState = 2;
        setTimeout(() => {
          messageEl.innerHTML = '';
        }, 1500);
        return;
      }

      await copyToClipboard(hexData.asciiOnly);
      messageEl.innerHTML = '<p>✅ ASCII скопирован!</p>';
      hexBtn.textContent = 'Показать HEX';
      setButtonColor(hexBtn, 'gray');
      hexState = 0;
      setTimeout(() => {
        messageEl.innerHTML = '';
      }, 1500);
    } catch (error) {
      messageEl.innerHTML = `<p class="error">Ошибка копирования: ${escapeHtml(String(error))}</p>`;
    }
  };
}

function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
