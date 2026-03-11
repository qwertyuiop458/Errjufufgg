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
});

searchEl.addEventListener('input', () => renderCategories(searchEl.value.trim().toLowerCase()));

function renderCategories(filter = '') {
  categoriesEl.innerHTML = '';
  previewEl.innerHTML = '';
  titleEl.textContent = 'Выберите файл';
  metaEl.textContent = '';
  actionsEl.classList.add('hidden');

  for (const [category, files] of Object.entries(currentData.categories)) {
    const visible = files.filter((f) => f.path.toLowerCase().includes(filter));
    if (!visible.length) continue;

    const wrapper = document.createElement('div');
    wrapper.className = 'category';

    const title = document.createElement('h3');
    title.textContent = `${category} (${visible.length})`;
    wrapper.appendChild(title);

    visible.forEach((file) => {
      const item = document.createElement('div');
      item.className = 'file-item';
      item.textContent = file.path;
      item.onclick = () => selectFile(file);
      wrapper.appendChild(item);
    });

    categoriesEl.appendChild(wrapper);
  }
}

async function selectFile(file) {
  decompiledText = '';

  const session = currentData.session_id;
  const encodedPath = file.path.split('/').map(encodeURIComponent).join('/');
  const artifactUrl = `/artifact/${session}/${encodedPath}`;
  const downloadUrl = `/download/${session}/${encodedPath}`;

  binaryState = null;

  titleEl.textContent = file.path;
  actionsEl.classList.remove('hidden');
  btnDownloadJavaEl.classList.add('hidden');
  btnBinaryEl.onclick = () => renderBinaryPreview(file, artifactUrl, downloadUrl);
  btnDecompileEl.onclick = () => renderDecompiledPreview(file, encodedPath, downloadUrl);
  btnDownloadJavaEl.onclick = () => downloadJava(file.path, decompiledText);
  previewEl.innerHTML = '';

  if (file.path.toLowerCase().endsWith('.class')) {
    await renderDecompiledPreview(file, encodedPath, downloadUrl);
    return;
  }

  await renderDefaultPreview(file, artifactUrl, downloadUrl);
}

async function renderDefaultPreview(file, artifactUrl, downloadUrl) {
  if (file.mime.startsWith('image/')) {
    previewEl.innerHTML = `<img src="${artifactUrl}" alt="${file.path}">`;
    setMeta(file, downloadUrl, 'Text');
    return;
  }

  if (file.mime.startsWith('audio/')) {
    previewEl.innerHTML = `<audio controls src="${artifactUrl}"></audio>`;
    return;
  }

  if (file.path.toLowerCase().endsWith('.class')) {
    previewEl.innerHTML = '<button id="decompile-btn" class="action-btn">Декомпилировать</button><div id="decompile-result"></div>';
    document.getElementById('decompile-btn').onclick = () => decompileClass(session, encodedPath);
    return;
  }

  if (file.previewable) {
    const text = await fetch(artifactUrl).then((r) => r.text());
    previewEl.innerHTML = `<pre class="code-box">${escapeHtml(text.slice(0, 20000))}</pre>`;
    return;
  }

  const bin = await fetch(artifactUrl).then((r) => r.arrayBuffer());
  const view = new Uint8Array(bin).slice(0, 256);
  previewEl.innerHTML = `<pre class="hex-box">${formatHex(view)}</pre>`;
}

async function decompileClass(session, encodedPath) {
  const resultEl = document.getElementById('decompile-result');
  resultEl.innerHTML = '<p>Декомпиляция...</p>';

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
    previewEl.innerHTML = `<pre>${escapeHtml(text.slice(0, 20000))}</pre>`;
    setMeta(file, downloadUrl, 'Text');
    return;
  }

  await renderBinaryPreview(file, artifactUrl, downloadUrl);
}

async function renderBinaryPreview(file, artifactUrl, downloadUrl) {
  decompiledText = '';
  btnDownloadJavaEl.classList.add('hidden');
  const response = await fetch(artifactUrl);
  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer).slice(0, 2048);
  const hexRows = [];
  for (let i = 0; i < bytes.length; i += 16) {
    const chunk = bytes.slice(i, i + 16);
    const offset = i.toString(16).padStart(8, '0');
    const hex = Array.from(chunk).map((b) => b.toString(16).padStart(2, '0')).join(' ');
    hexRows.push(`${offset}: ${hex}`);
  }
  previewEl.innerHTML = `<pre>${escapeHtml(hexRows.join('\n'))}</pre>`;
  setMeta(file, downloadUrl, 'Binary (hex)');
}

async function renderDecompiledPreview(file, encodedPath, downloadUrl) {
  const session = currentData.session_id;
  const response = await fetch(`/decompile/${session}/${encodedPath}`);

  if (!response.ok) {
    previewEl.innerHTML = [
      '<p>Не удалось декомпилировать.</p>',
      '<p>Попробуйте открыть файл как бинарный.</p>',
      '<button id="fallback-binary" type="button">Открыть как бинарный</button>',
    ].join('');
    document.getElementById('fallback-binary').onclick = () => {
      const artifactUrl = `/artifact/${session}/${encodedPath}`;
      renderBinaryPreview(file, artifactUrl, downloadUrl);
    };
    btnDownloadJavaEl.classList.add('hidden');
    setMeta(file, downloadUrl, 'Binary (hex)');
    return;
  }

  const payload = await response.json();
  decompiledText = payload.java_source || '';
  previewEl.innerHTML = `<pre>${escapeHtml(decompiledText)}</pre>`;
  btnDownloadJavaEl.classList.remove('hidden');
  setMeta(file, downloadUrl, 'Decompiled Java');
}

function setMeta(file, downloadUrl, previewMode) {
  metaEl.innerHTML = `Размер: ${file.size} байт<br>SHA1: ${file.sha1}<br>MIME: ${file.mime}<br>Preview: ${previewMode}<br><a href="${downloadUrl}">Скачать файл</a>`;
}

function downloadJava(path, source) {
  if (!source) {
    return;
  }
  const baseName = path.split('/').pop().replace(/\.class$/i, '.java');
  const blob = new Blob([source], { type: 'text/x-java-source;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = baseName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
