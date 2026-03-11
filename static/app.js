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

async function selectFile(file, itemNode) {
  clearSelectedFile();
  if (itemNode) itemNode.classList.add('active');

  const session = currentData.session_id;
  const encodedPath = file.path.split('/').map(encodeURIComponent).join('/');
  const artifactUrl = `/artifact/${session}/${encodedPath}`;
  const downloadUrl = `/download/${session}/${encodedPath}`;

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

  if (file.path.toLowerCase().endsWith('.class')) {
    previewEl.innerHTML = `
      <div class="actions-row">
        <button id="decompile-btn" class="action-btn" type="button">Декомпилировать .class</button>
        <button id="show-hex-btn" class="action-btn secondary" type="button">Показать HEX</button>
      </div>
      <div id="decompile-result"></div>
    `;

    document.getElementById('decompile-btn').onclick = () => decompileClass(session, encodedPath);
    document.getElementById('show-hex-btn').onclick = async () => {
      const buf = await fetch(artifactUrl).then((r) => r.arrayBuffer());
      document.getElementById('decompile-result').innerHTML = `<pre class="hex-box">${escapeHtml(formatHex(new Uint8Array(buf).slice(0, 1024)))}</pre>`;
    };

    await decompileClass(session, encodedPath);
    return;
  }

  if (file.previewable) {
    const text = await fetch(artifactUrl).then((r) => r.text());
    previewEl.innerHTML = `<pre class="code-box">${escapeHtml(text.slice(0, 50000))}</pre>`;
    return;
  }

  const bin = await fetch(artifactUrl).then((r) => r.arrayBuffer());
  const view = new Uint8Array(bin).slice(0, 1024);
  previewEl.innerHTML = `<pre class="hex-box">${escapeHtml(formatHex(view))}</pre>`;
}

async function decompileClass(session, encodedPath) {
  const resultEl = document.getElementById('decompile-result');
  resultEl.innerHTML = '<p>Декомпиляция...</p>';

  try {
    const response = await fetch(`/decompile/${session}/${encodedPath}`);
    const data = await response.json();

    if (data.java_source) {
      resultEl.innerHTML = `<pre class="code-box">${escapeHtml(data.java_source)}</pre>`;
      return;
    }

    resultEl.innerHTML = `<p class="error">${escapeHtml(data.error || 'Не удалось декомпилировать')}</p>`;
    if (data.hex_preview) {
      resultEl.innerHTML += `<pre class="hex-box">${escapeHtml(data.hex_preview)}</pre>`;
    }
  } catch (error) {
    resultEl.innerHTML = `<p class="error">Сетевая ошибка: ${escapeHtml(String(error))}</p>`;
  }
}

function formatHex(uint8) {
  let out = '';
  for (let i = 0; i < uint8.length; i += 16) {
    const chunk = Array.from(uint8.slice(i, i + 16));
    const hex = chunk.map((b) => b.toString(16).padStart(2, '0')).join(' ');
    const ascii = chunk.map((b) => (b >= 32 && b <= 126 ? String.fromCharCode(b) : '.')).join('');
    out += `${i.toString(16).padStart(8, '0')}  ${hex.padEnd(47, ' ')}  ${ascii}\n`;
  }
  return out;
}

function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
