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

  previewEl.textContent = 'Для этого типа предпросмотр недоступен. Скачайте файл для оффлайн-анализа.';
}

function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
