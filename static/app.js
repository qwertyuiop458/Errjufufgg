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

function setupAudioControls(container, src, hintDuration = null) {
  const audio = container.querySelector('audio');
  const playBtn = container.querySelector('.play-btn');
  const stopBtn = container.querySelector('.stop-btn');
  const volume = container.querySelector('.volume-slider');
  const progress = container.querySelector('.progress-slider');
  const loopBox = container.querySelector('.loop-box');
  const timeEl = container.querySelector('.time-label');

  audio.src = src;
  audio.preload = 'metadata';

  const fmt = (t) => {
    if (!Number.isFinite(t) || t < 0) return '--:--';
    const m = Math.floor(t / 60);
    const s = Math.floor(t % 60);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  const updateTime = () => {
    const total = Number.isFinite(audio.duration) ? audio.duration : hintDuration;
    timeEl.textContent = `${fmt(audio.currentTime)} / ${fmt(total)}`;
    if (Number.isFinite(audio.duration) && audio.duration > 0) {
      progress.value = String(Math.round((audio.currentTime / audio.duration) * 1000));
    }
  };

  playBtn.onclick = async () => {
    try {
      if (audio.paused) {
        await audio.play();
        playBtn.textContent = '⏸ Пауза';
      } else {
        audio.pause();
        playBtn.textContent = '▶️ Play';
      }
    } catch (error) {
      timeEl.textContent = `Ошибка воспроизведения: ${error}`;
    }
  };

  stopBtn.onclick = () => {
    audio.pause();
    audio.currentTime = 0;
    playBtn.textContent = '▶️ Play';
    updateTime();
  };

  volume.oninput = () => {
    audio.volume = Number(volume.value);
  };

  progress.oninput = () => {
    if (Number.isFinite(audio.duration)) {
      audio.currentTime = (Number(progress.value) / 1000) * audio.duration;
    }
  };

  loopBox.onchange = () => {
    audio.loop = loopBox.checked;
  };

  audio.addEventListener('timeupdate', updateTime);
  audio.addEventListener('loadedmetadata', updateTime);
  audio.addEventListener('ended', () => {
    playBtn.textContent = '▶️ Play';
  });

  updateTime();
}

function renderPlayerHost(resultPanel, src, format, durationSeconds = null) {
  resultPanel.innerHTML = `
    <div class="audio-card">
      <p><strong>Формат:</strong> ${escapeHtml(String(format).toUpperCase())} ${durationSeconds ? `• ${durationSeconds} сек` : ''}</p>
      <audio controls></audio>
      <div class="actions-row">
        <button type="button" class="action-btn play-btn">▶️ Play</button>
        <button type="button" class="action-btn secondary stop-btn">⏹ Stop</button>
        <label>Volume <input class="volume-slider" type="range" min="0" max="1" step="0.01" value="1"></label>
        <label>Progress <input class="progress-slider" type="range" min="0" max="1000" step="1" value="0"></label>
        <label><input class="loop-box" type="checkbox"> Loop</label>
      </div>
      <p class="time-label">00:00 / --:--</p>
    </div>
  `;

  setupAudioControls(resultPanel.querySelector('.audio-card'), src, durationSeconds);
}

async function selectFile(file, itemNode) {
  clearSelectedFile();
  if (itemNode) itemNode.classList.add('active');

  const session = currentData.session_id;
  const encodedPath = file.path.split('/').map(encodeURIComponent).join('/');
  const artifactUrl = `/artifact/${session}/${encodedPath}`;
  const downloadUrl = `/download/${session}/${encodedPath}`;
  const isClassFile = file.path.toLowerCase().endsWith('.class');
  const knownAudio = Boolean(file.audio_detected || file.mime.startsWith('audio/'));

  titleEl.textContent = file.path;
  metaEl.innerHTML = `
    <div class="meta-grid">
      <span>Размер:</span><strong>${file.size} байт</strong>
      <span>SHA1:</span><strong class="sha-cell">${file.sha1}</strong>
      <span>MIME:</span><strong>${file.mime}</strong>
    </div>
    <a class="download-link" href="${downloadUrl}">💾 Скачать</a>
  `;
  previewEl.innerHTML = '';

  if (file.mime.startsWith('image/')) {
    previewEl.innerHTML = `<img src="${artifactUrl}" alt="${file.path}">`;
    return;
  }

  if (file.previewable && !isClassFile && !knownAudio) {
    const text = await fetch(artifactUrl).then((r) => r.text());
    previewEl.innerHTML = `<pre class="code-box">${escapeHtml(text.slice(0, 50000))}</pre>`;
    return;
  }

  const bin = await fetch(artifactUrl).then((r) => r.arrayBuffer());
  const view = new Uint8Array(bin).slice(0, 2048);
  const hexData = formatHexData(view);

  previewEl.innerHTML = `
    <div class="actions-row" id="file-actions"></div>
    <div id="preview-message"></div>
    <div id="result-panel"></div>
  `;

  const actions = document.getElementById('file-actions');
  const resultPanel = document.getElementById('result-panel');
  const messageEl = document.getElementById('preview-message');

  const hexBtn = document.createElement('button');
  hexBtn.className = 'action-btn';
  hexBtn.type = 'button';
  hexBtn.textContent = 'Показать HEX';
  setButtonColor(hexBtn, 'gray');
  actions.appendChild(hexBtn);

  let hexState = 0;
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
        setTimeout(() => { messageEl.innerHTML = ''; }, 1500);
        return;
      }
      await copyToClipboard(hexData.asciiOnly);
      messageEl.innerHTML = '<p>✅ ASCII скопирован!</p>';
      hexBtn.textContent = 'Показать HEX';
      setButtonColor(hexBtn, 'gray');
      hexState = 0;
      setTimeout(() => { messageEl.innerHTML = ''; }, 1500);
    } catch (error) {
      messageEl.innerHTML = `<p class="error">Ошибка копирования: ${escapeHtml(String(error))}</p>`;
    }
  };

  if (isClassFile) {
    const decompileBtn = document.createElement('button');
    decompileBtn.className = 'action-btn';
    decompileBtn.type = 'button';
    decompileBtn.textContent = 'Декомпилировать .class';
    setButtonColor(decompileBtn, 'gray');
    actions.prepend(decompileBtn);

    let javaSource = '';
    let javaLoaded = false;

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
        decompileBtn.textContent = '✅ Скопировано!';
        setTimeout(() => {
          decompileBtn.textContent = '📋 Копировать Java код';
        }, 1500);
      } catch (error) {
        resultPanel.innerHTML = `<p class="error">Ошибка копирования: ${escapeHtml(String(error))}</p>`;
      }
    };

    return;
  }

  if (knownAudio) {
    const listenBtn = document.createElement('button');
    listenBtn.className = 'action-btn';
    listenBtn.type = 'button';
    listenBtn.textContent = '▶️ Слушать';
    actions.prepend(listenBtn);

    listenBtn.onclick = async () => {
      const probeResp = await fetch(`/audio_probe/${session}/${encodedPath}`).then((r) => r.json());
      if (!probeResp.found) {
        messageEl.innerHTML = '<p class="error">Музыкальная сигнатура не найдена</p>';
        return;
      }

      messageEl.innerHTML = `<p>Найден ${escapeHtml(probeResp.format.toUpperCase())} @ ${probeResp.offset_hex}</p>`;
      const streamUrl = `/audio_stream/${session}/${encodedPath}?offset=${probeResp.offset}`;
      renderPlayerHost(resultPanel, streamUrl, probeResp.format, probeResp.duration_seconds);
    };

    return;
  }

  const findAudioBtn = document.createElement('button');
  findAudioBtn.className = 'action-btn secondary';
  findAudioBtn.type = 'button';
  findAudioBtn.textContent = '🔍 Найти музыку';
  actions.prepend(findAudioBtn);

  findAudioBtn.onclick = async () => {
    try {
      const probeResp = await fetch(`/audio_probe/${session}/${encodedPath}`).then((r) => r.json());
      if (!probeResp.found) {
        messageEl.innerHTML = '<p>Музыкальные сигнатуры не найдены в первых 1024 байтах.</p>';
        return;
      }

      messageEl.innerHTML = `<p>Найден ${escapeHtml(probeResp.format.toUpperCase())} @ ${probeResp.offset_hex}</p>`;

      const listenBtn = document.createElement('button');
      listenBtn.className = 'action-btn';
      listenBtn.type = 'button';
      listenBtn.textContent = '▶️ Слушать';
      if (!actions.querySelector('.listen-dynamic')) {
        listenBtn.classList.add('listen-dynamic');
        actions.insertBefore(listenBtn, actions.firstChild);
      }

      listenBtn.onclick = () => {
        const streamUrl = `/audio_stream/${session}/${encodedPath}?offset=${probeResp.offset}`;
        renderPlayerHost(resultPanel, streamUrl, probeResp.format, probeResp.duration_seconds);
      };
    } catch (error) {
      messageEl.innerHTML = `<p class="error">Ошибка аудиоанализа: ${escapeHtml(String(error))}</p>`;
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
