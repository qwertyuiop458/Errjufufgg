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

  const sorted = Object.entries(currentData.categories).sort((a, b) => {
    if (a[0] === '🎵 Аудио') return -1;
    if (b[0] === '🎵 Аудио') return 1;
    return a[0].localeCompare(b[0]);
  });

  for (const [category, files] of sorted) {
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

function readVarLen(data, offset) {
  let value = 0;
  let i = offset;
  while (i < data.length) {
    value = (value << 7) | (data[i] & 0x7f);
    if ((data[i] & 0x80) === 0) return { value, next: i + 1 };
    i += 1;
  }
  return { value: 0, next: offset + 1 };
}

function parseMidiNotes(arrayBuffer) {
  const data = new Uint8Array(arrayBuffer);
  if (data.length < 14 || String.fromCharCode(...data.slice(0, 4)) !== 'MThd') return [];

  let ptr = 14;
  const notes = [];
  while (ptr + 8 < data.length) {
    const id = String.fromCharCode(...data.slice(ptr, ptr + 4));
    const len = (data[ptr + 4] << 24) | (data[ptr + 5] << 16) | (data[ptr + 6] << 8) | data[ptr + 7];
    ptr += 8;
    if (id !== 'MTrk') {
      ptr += len;
      continue;
    }

    const end = ptr + len;
    let time = 0;
    let running = 0;
    const active = {};

    while (ptr < end && ptr < data.length) {
      const delta = readVarLen(data, ptr);
      time += delta.value;
      ptr = delta.next;
      if (ptr >= end) break;

      let status = data[ptr];
      if (status < 0x80) {
        status = running;
      } else {
        running = status;
        ptr += 1;
      }

      const cmd = status & 0xf0;
      if (cmd === 0x90 || cmd === 0x80) {
        const note = data[ptr++];
        const vel = data[ptr++];
        const key = `${status & 0x0f}-${note}`;

        if (cmd === 0x90 && vel > 0) {
          active[key] = { start: time, note };
        } else if (active[key]) {
          notes.push({ note, start: active[key].start, end: time });
          delete active[key];
        }
      } else if (cmd === 0xa0 || cmd === 0xb0 || cmd === 0xe0) {
        ptr += 2;
      } else if (cmd === 0xc0 || cmd === 0xd0) {
        ptr += 1;
      } else if (status === 0xff) {
        ptr += 1;
        const vlq = readVarLen(data, ptr);
        ptr = vlq.next + vlq.value;
      } else if (status === 0xf0 || status === 0xf7) {
        const vlq = readVarLen(data, ptr);
        ptr = vlq.next + vlq.value;
      } else {
        break;
      }
    }

    break;
  }

  return notes.slice(0, 256);
}

function drawMidiRoll(canvas, notes) {
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#0f1322';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (!notes.length) {
    ctx.fillStyle = '#9bb0ff';
    ctx.fillText('MIDI ноты не обнаружены', 10, 20);
    return;
  }

  const maxEnd = Math.max(...notes.map((n) => n.end), 1);
  const minNote = Math.min(...notes.map((n) => n.note));
  const maxNote = Math.max(...notes.map((n) => n.note));
  const noteRange = Math.max(maxNote - minNote + 1, 1);

  for (const n of notes) {
    const x = (n.start / maxEnd) * canvas.width;
    const w = Math.max(((n.end - n.start) / maxEnd) * canvas.width, 2);
    const y = canvas.height - ((n.note - minNote + 1) / noteRange) * canvas.height;
    ctx.fillStyle = '#3f63ff';
    ctx.fillRect(x, y, w, 4);
  }
}

async function drawWaveform(canvas, audioBuffer) {
  const ctx = canvas.getContext('2d');
  const data = audioBuffer.getChannelData(0);
  const width = canvas.width;
  const height = canvas.height;
  const step = Math.ceil(data.length / width);

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#0f1322';
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = '#3f63ff';
  ctx.lineWidth = 1;
  ctx.beginPath();

  for (let x = 0; x < width; x++) {
    let min = 1;
    let max = -1;
    for (let j = 0; j < step; j++) {
      const datum = data[x * step + j] || 0;
      if (datum < min) min = datum;
      if (datum > max) max = datum;
    }
    ctx.moveTo(x, (1 + min) * 0.5 * height);
    ctx.lineTo(x, (1 + max) * 0.5 * height);
  }
  ctx.stroke();
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
  return audio;
}

async function renderPlayerHost(resultPanel, src, format, durationSeconds = null, extractedBuffer = null) {
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
      <canvas class="audio-visual" width="900" height="130"></canvas>
      <a id="save-audio" class="download-link" href="#" download>Сохранить как...</a>
    </div>
  `;

  const audio = setupAudioControls(resultPanel.querySelector('.audio-card'), src, durationSeconds);
  const canvas = resultPanel.querySelector('.audio-visual');
  const saveLink = resultPanel.querySelector('#save-audio');

  saveLink.href = src;

  if (format === 'wav' && extractedBuffer) {
    try {
      const actx = new AudioContext();
      const decoded = await actx.decodeAudioData(extractedBuffer.slice(0));
      await drawWaveform(canvas, decoded);
      actx.close();
    } catch {
      canvas.getContext('2d').fillText('Waveform недоступен', 10, 20);
    }
  } else if (format === 'midi' && extractedBuffer) {
    const notes = parseMidiNotes(extractedBuffer);
    drawMidiRoll(canvas, notes);
  } else {
    canvas.getContext('2d').fillText('Визуализация доступна для WAV/MIDI', 10, 20);
  }

  audio.addEventListener('error', async () => {
    resultPanel.insertAdjacentHTML(
      'afterbegin',
      '<p class="error">Формат не поддержан браузером, пробуем конвертацию в WAV...</p>'
    );
  }, { once: true });
}

function createHexCycleButton(actions, resultPanel, messageEl, hexData) {
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
      ${file.audio_detected ? `<span>Audio:</span><strong>${file.audio_format || ''} @ 0x${(file.audio_offset || 0).toString(16).padStart(8, '0')}</strong>` : ''}
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

  const fullBuffer = await fetch(artifactUrl).then((r) => r.arrayBuffer());
  const view = new Uint8Array(fullBuffer).slice(0, 2048);
  const hexData = formatHexData(view);

  previewEl.innerHTML = `
    <div class="actions-row" id="file-actions"></div>
    <div id="preview-message"></div>
    <div id="result-panel"></div>
  `;

  const actions = document.getElementById('file-actions');
  const resultPanel = document.getElementById('result-panel');
  const messageEl = document.getElementById('preview-message');

  createHexCycleButton(actions, resultPanel, messageEl, hexData);

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

  const scanBtn = document.createElement('button');
  scanBtn.className = 'action-btn secondary';
  scanBtn.type = 'button';
  scanBtn.textContent = '🔍 Найти аудио';
  actions.prepend(scanBtn);

  scanBtn.onclick = async () => {
    try {
      scanBtn.disabled = true;
      scanBtn.textContent = 'Сканирование...';
      const scan = await fetch(`/audio_scan/${session}/${encodedPath}`).then((r) => r.json());
      scanBtn.disabled = false;
      scanBtn.textContent = '🔍 Найти аудио';

      if (!scan.found || !scan.signatures?.length) {
        messageEl.innerHTML = '<p>Аудиосигнатуры не найдены.</p>';
        return;
      }

      const rows = scan.signatures
        .map(
          (s) => `<li><strong>${escapeHtml(String(s.format).toUpperCase())}</strong> (${escapeHtml(String(s.signature))}) @ ${escapeHtml(String(s.offset_hex))}
          <button class="action-btn extract-btn" data-offset="${s.offset}" data-format="${s.format}" type="button">Извлечь и слушать</button></li>`
        )
        .join('');

      resultPanel.innerHTML = `<ul class="sig-list">${rows}</ul>`;

      resultPanel.querySelectorAll('.extract-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const offset = btn.getAttribute('data-offset');
          const format = btn.getAttribute('data-format');

          const meta = await fetch(`/audio_extract/${session}/${encodedPath}?offset=${offset}&mode=json`).then((r) => r.json());
          if (!meta.ok) {
            messageEl.innerHTML = `<p class="error">${escapeHtml(meta.error || 'Ошибка извлечения')}</p>`;
            return;
          }

          let streamUrl = `/audio_extract/${session}/${encodedPath}?offset=${offset}`;
          if (format === 'amr' || format === 'midi') {
            streamUrl += '&convert=1';
          }

          let extractedBuffer = null;
          try {
            extractedBuffer = await fetch(streamUrl).then((r) => r.arrayBuffer());
          } catch {
            extractedBuffer = null;
          }

          await renderPlayerHost(
            resultPanel,
            streamUrl,
            format === 'amr' ? 'wav' : format,
            null,
            extractedBuffer
          );

          const save = resultPanel.querySelector('#save-audio');
          if (save) {
            save.href = `${streamUrl}&download=1`;
            save.textContent = 'Сохранить как...';
          }
        });
      });
    } catch (error) {
      scanBtn.disabled = false;
      scanBtn.textContent = '🔍 Найти аудио';
      messageEl.innerHTML = `<p class="error">Ошибка аудиосканера: ${escapeHtml(String(error))}</p>`;
    }
  };

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

      let streamUrl = `/audio_extract/${session}/${encodedPath}?offset=${probeResp.offset}`;
      if (probeResp.format === 'amr' || probeResp.format === 'midi') {
        streamUrl += '&convert=1';
      }

      const extractedBuffer = await fetch(streamUrl).then((r) => r.arrayBuffer());
      await renderPlayerHost(
        resultPanel,
        streamUrl,
        probeResp.format === 'amr' ? 'wav' : probeResp.format,
        probeResp.duration_seconds,
        extractedBuffer
      );

      const save = resultPanel.querySelector('#save-audio');
      if (save) {
        save.href = `${streamUrl}&download=1`;
      }

      messageEl.innerHTML = `<p>Найден ${escapeHtml(probeResp.format.toUpperCase())} @ ${probeResp.offset_hex}</p>`;
    };
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
