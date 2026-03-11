const form = document.getElementById('upload-form');
const statusEl = document.getElementById('status');
const workspace = document.getElementById('workspace');
const categoriesEl = document.getElementById('categories');
const previewEl = document.getElementById('preview');
const titleEl = document.getElementById('title');
const metaEl = document.getElementById('meta');
const searchEl = document.getElementById('search');

const BINARY_PAGE_SIZE = 4096;

let currentData = null;
let binaryState = null;

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
  const session = currentData.session_id;
  const encodedPath = file.path.split('/').map(encodeURIComponent).join('/');
  const artifactUrl = `/artifact/${session}/${encodedPath}`;
  const downloadUrl = `/download/${session}/${encodedPath}`;

  binaryState = null;

  titleEl.textContent = file.path;
  metaEl.innerHTML = `Размер: ${file.size} байт<br>SHA1: ${file.sha1}<br>MIME: ${file.mime}<br><a href="${downloadUrl}">Скачать файл</a>`;
  previewEl.innerHTML = '';

  if (file.mime.startsWith('image/')) {
    previewEl.innerHTML = `<img src="${artifactUrl}" alt="${file.path}">`;
    return;
  }

  if (file.mime.startsWith('audio/')) {
    previewEl.innerHTML = `<audio controls src="${artifactUrl}"></audio>`;
    return;
  }

  if (file.previewable) {
    const text = await fetch(artifactUrl).then((r) => r.text());
    previewEl.innerHTML = `<pre>${escapeHtml(text.slice(0, 20000))}</pre>`;
    return;
  }

  if (file.path.toLowerCase().endsWith('.class')) {
    renderClassPreviewActions(file);
    return;
  }

  setupBinaryPreview(file);
  await loadBinaryChunk(0);
}

function renderClassPreviewActions(file) {
  const actions = document.createElement('div');
  actions.className = 'binary-actions';

  const javaBtn = document.createElement('button');
  javaBtn.textContent = 'Показать Java (декомпиляция)';
  javaBtn.onclick = () => {
    previewEl.innerHTML = '<p>Декомпиляция .class пока недоступна в этом интерфейсе.</p>';
    previewEl.appendChild(actions);
  };

  const hexBtn = document.createElement('button');
  hexBtn.textContent = 'Показать hex';
  hexBtn.onclick = async () => {
    setupBinaryPreview(file);
    await loadBinaryChunk(0);
  };

  actions.appendChild(javaBtn);
  actions.appendChild(hexBtn);

  previewEl.innerHTML = '<p>Для .class приоритетен Java-просмотр. При необходимости можно открыть hex.</p>';
  previewEl.appendChild(actions);
}

function setupBinaryPreview(file) {
  binaryState = {
    file,
    offset: 0,
    length: BINARY_PAGE_SIZE,
  };
}

async function loadBinaryChunk(offset) {
  if (!binaryState) return;

  const session = currentData.session_id;
  const encodedPath = binaryState.file.path.split('/').map(encodeURIComponent).join('/');
  const url = `/binary/${session}/${encodedPath}?offset=${offset}&length=${binaryState.length}`;

  const response = await fetch(url);
  const data = await response.json();

  if (!response.ok) {
    previewEl.textContent = data.error || 'Ошибка бинарного предпросмотра';
    return;
  }

  binaryState.offset = data.offset;
  renderHexTable(data);
}

function renderHexTable(data) {
  const lines = data.hex_lines.map((hexLine, idx) => {
    const lineOffset = data.offset + idx * 16;
    const addr = lineOffset.toString(16).padStart(8, '0');
    const ascii = data.ascii_lines[idx] || '';
    return `<tr><td>${addr}</td><td>${escapeHtml(hexLine)}</td><td>${escapeHtml(ascii)}</td></tr>`;
  }).join('');

  const prevDisabled = data.offset === 0 ? 'disabled' : '';
  const nextDisabled = data.offset + data.length >= data.total_size ? 'disabled' : '';

  previewEl.innerHTML = `
    <div class="binary-actions">
      <button id="prev-block" ${prevDisabled}>Предыдущий блок</button>
      <button id="next-block" ${nextDisabled}>Следующий блок</button>
      <span class="binary-hint">offset ${data.offset}..${data.offset + data.length} из ${data.total_size}</span>
    </div>
    <div class="hex-wrap">
      <table class="hex-table">
        <thead><tr><th>Адрес</th><th>HEX</th><th>ASCII</th></tr></thead>
        <tbody>${lines}</tbody>
      </table>
    </div>
  `;

  const prevBtn = document.getElementById('prev-block');
  const nextBtn = document.getElementById('next-block');
  prevBtn.onclick = () => loadBinaryChunk(Math.max(0, data.offset - binaryState.length));
  nextBtn.onclick = () => loadBinaryChunk(data.offset + data.length);
}

function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
