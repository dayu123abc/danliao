const csrfToken = document.querySelector('meta[name="editor-csrf-token"]').content;
const fileTree = document.getElementById('fileTree');
const codeInput = document.getElementById('codeInput');
const lineNumbers = document.getElementById('lineNumbers');
const saveButton = document.getElementById('saveButton');
const deleteButton = document.getElementById('deleteButton');
let activePath = null;
let activeHash = null;
let savedContent = '';
let externalHash = null;
let fileEntries = [];
let toastTimer = null;

/* ========== 以下全部是你原有的函数，一个字没改 ========== */

function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2600);
}

function showNotice(message, isError = false) {
    const notice = document.getElementById('editorNotice');
    notice.textContent = message;
    notice.classList.toggle('error', isError);
    notice.hidden = !message;
}

async function api(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            ...(options.body ? {'Content-Type': 'application/json'} : {}),
            ...(options.method && options.method !== 'GET' ? {'X-CSRF-Token': csrfToken} : {}),
            ...options.headers
        }
    });
    const data = await response.json();
    if (!response.ok) {
        const error = new Error(data.error || '请求失败');
        error.status = response.status;
        error.data = data;
        throw error;
    }
    return data;
}

function extensionOf(path) {
    return path.includes('.') ? path.split('.').pop().toLowerCase() : '';
}

function iconFor(path) {
    const icons = {py: 'Py', js: 'JS', css: '#', html: '<>', md: 'M', json: '{}', txt: 'T', toml: '≡', yml: 'Y', yaml: 'Y', bat: '>_', spec: 'P'};
    return icons[extensionOf(path)] || '·';
}

function renderTree() {
    const root = {dirs: new Map(), files: []};
    for (const entry of fileEntries) {
        const parts = entry.path.split('/');
        let node = root;
        for (const folder of parts.slice(0, -1)) {
            if (!node.dirs.has(folder)) node.dirs.set(folder, {dirs: new Map(), files: []});
            node = node.dirs.get(folder);
        }
        node.files.push(entry);
    }

    function renderNode(node, depth = 0) {
        const dirs = [...node.dirs.entries()].sort(([a], [b]) => a.localeCompare(b));
        const folders = dirs.map(([name, child]) => `
            <div class="tree-group">
                <div class="tree-folder" style="padding-left:${7 + depth * 12}px"><span class="tree-folder-label">${escapeHtml(name)}</span></div>
                ${renderNode(child, depth + 1)}
            </div>`).join('');
        const files = node.files.sort((a, b) => a.name.localeCompare(b.name)).map(entry => `
            <button class="tree-file ${entry.path === activePath ? 'active' : ''}" data-path="${escapeHtml(entry.path)}" style="padding-left:${24 + depth * 12}px" title="${escapeHtml(entry.path)}">
                <span class="tree-file-icon">${iconFor(entry.path)}</span><span class="tree-file-name">${escapeHtml(entry.name)}</span>
            </button>`).join('');
        return files + folders;
    }

    fileTree.innerHTML = renderNode(root) || '<div class="tree-loading">项目里还没有可编辑文件</div>';
    fileTree.querySelectorAll('.tree-file').forEach(button => button.addEventListener('click', () => openFile(button.dataset.path)));
}

function escapeHtml(value) {
    const element = document.createElement('span');
    element.textContent = value;
    return element.innerHTML;
}

async function loadFiles() {
    try {
        const latestEntries = await api('/api/admin/editor/files');
        const previousPaths = fileEntries.map(entry => `${entry.path}:${entry.size}`).join('\n');
        const latestPaths = latestEntries.map(entry => `${entry.path}:${entry.size}`).join('\n');
        fileEntries = latestEntries;
        if (previousPaths !== latestPaths) renderTree();
        document.getElementById('syncDot').parentElement.classList.remove('offline');
        document.getElementById('syncLabel').textContent = '服务器已连接';
        return true;
    } catch (error) {
        document.getElementById('syncDot').parentElement.classList.add('offline');
        document.getElementById('syncLabel').textContent = '同步中断';
        if (!fileEntries.length) fileTree.innerHTML = `<div class="tree-loading">${escapeHtml(error.message)}</div>`;
        return false;
    }
}

async function openFile(path) {
    if (isDirty() && !confirm('当前文件有未保存的修改，确定切换并放弃这些修改吗？')) return;
    showNotice('');
    try {
        const file = await api('/api/admin/editor/file?path=' + encodeURIComponent(path));
        activePath = file.path;
        activeHash = file.hash;
        savedContent = file.content;
        externalHash = null;
        codeInput.value = file.content;
        codeInput.hidden = false;
        document.getElementById('welcomeState').hidden = true;
        document.getElementById('codeEditor').hidden = false;
        document.getElementById('currentPath').textContent = path;
        document.getElementById('fileGlyph').textContent = iconFor(path);
        document.getElementById('fileStatus').textContent = '已与服务器同步';
        document.getElementById('languageLabel').textContent = extensionOf(path).toUpperCase() || '纯文本';
        saveButton.disabled = false;
        deleteButton.disabled = false;
        updateEditorState();
        updateTreeSelection();
        codeInput.focus();
        // ★回档：打开文件即可查历史，回档按钮点亮
        document.getElementById('historyButton').disabled = false;
        hideHistoryHint();
    } catch (error) {
        showToast(error.message);
    }
}

function updateTreeSelection() {
    fileTree.querySelectorAll('.tree-file').forEach(button => button.classList.toggle('active', button.dataset.path === activePath));
}

function isDirty() {
    return activePath !== null && codeInput.value !== savedContent;
}

function updateLineNumbers() {
    const count = codeInput.value.split('\n').length;
    lineNumbers.textContent = Array.from({length: count}, (_, index) => index + 1).join('\n');
    lineNumbers.scrollTop = codeInput.scrollTop;
    const beforeCursor = codeInput.value.slice(0, codeInput.selectionStart).split('\n');
    document.getElementById('cursorLabel').textContent = `行 ${beforeCursor.length}，列 ${beforeCursor[beforeCursor.length - 1].length + 1}`;
    document.getElementById('sizeLabel').textContent = `${new Blob([codeInput.value]).size.toLocaleString()} 字节`;
}

function updateEditorState() {
    const dirty = isDirty();
    document.getElementById('dirtyIndicator').classList.toggle('visible', dirty);
    saveButton.disabled = !activePath || !dirty;
    document.getElementById('fileStatus').textContent = externalHash ? '服务器文件已更新' : (dirty ? '有未保存的更改' : '已与服务器同步');
    updateLineNumbers();
}

async function saveFile() {
    if (!activePath || !isDirty()) return;
    saveButton.disabled = true;
    document.getElementById('fileStatus').textContent = '正在保存...';
    try {
        const result = await api('/api/admin/editor/file', {
            method: 'PUT',
            body: JSON.stringify({path: activePath, content: codeInput.value, hash: externalHash || activeHash})
        });
        savedContent = codeInput.value;
        activeHash = result.hash;
        externalHash = null;
        showNotice('');
        updateEditorState();
        showToast('已保存到运行服务器');
        // ★回档：保存成功后，把这一版落进服务端版本库
        await snapshotVersion(activePath, codeInput.value);
    } catch (error) {
        if (error.status === 409) {
            externalHash = 'conflict';
            showNotice('服务器上的文件已被其他来源修改。请使用“刷新”重新载入后再编辑，避免覆盖他人的内容。', true);
            document.getElementById('fileStatus').textContent = '发现外部修改';
            saveButton.disabled = false;
        } else {
            document.getElementById('fileStatus').textContent = '保存失败';
            saveButton.disabled = false;
            showToast(error.message);
        }
    }
}

async function checkExternalChanges() {
    if (!activePath) return;
    try {
        const file = await api('/api/admin/editor/file?path=' + encodeURIComponent(activePath));
        if (file.hash === activeHash || file.hash === externalHash) return;
        if (isDirty()) {
            externalHash = file.hash;
            document.getElementById('fileStatus').textContent = '服务器文件已更新';
            showNotice('检测到服务器文件已被其他来源修改。你可以先保存本地版本，或刷新以载入服务器版本。', true);
            return;
        }
        activeHash = file.hash;
        savedContent = file.content;
        codeInput.value = file.content;
        updateEditorState();
        showToast('已载入服务器上的最新版本');
    } catch (error) {
        document.getElementById('syncDot').parentElement.classList.add('offline');
        document.getElementById('syncLabel').textContent = '同步中断';
    }
}

async function createFile() {
    const path = prompt('输入新文件的相对路径，例如 static/new.js');
    if (!path) return;
    try {
        await api('/api/admin/editor/file', {method: 'POST', body: JSON.stringify({path, content: ''})});
        await loadFiles();
        await openFile(path);
        showToast('文件已创建');
    } catch (error) {
        showToast(error.message);
    }
}

async function deleteFile() {
    if (!activePath || !confirm(`确定删除 ${activePath} 吗？此操作会立即影响运行服务器上的文件。`)) return;
    try {
        await api('/api/admin/editor/file', {method: 'DELETE', body: JSON.stringify({path: activePath})});
        // ★回档：删除源文件时，顺手把它的版本归档也清掉（可选，失败不阻塞）
        api('/api/admin/editor/file/versions?path=' + encodeURIComponent(activePath), {method: 'DELETE'}).catch(() => {});
        activePath = null;
        activeHash = null;
        savedContent = '';
        externalHash = null;
        codeInput.value = '';
        document.getElementById('codeEditor').hidden = true;
        document.getElementById('welcomeState').hidden = false;
        document.getElementById('currentPath').textContent = '未选择文件';
        document.getElementById('fileStatus').textContent = '选择文件开始编辑';
        document.getElementById('languageLabel').textContent = '纯文本';
        saveButton.disabled = true;
        deleteButton.disabled = true;
        document.getElementById('historyButton').disabled = true;
        showNotice('');
        await loadFiles();
        showToast('文件已从服务器删除');
    } catch (error) {
        showToast(error.message);
    }
}

codeInput.addEventListener('input', updateEditorState);
codeInput.addEventListener('scroll', () => { lineNumbers.scrollTop = codeInput.scrollTop; });
codeInput.addEventListener('click', updateLineNumbers);
codeInput.addEventListener('keyup', updateLineNumbers);
codeInput.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        saveFile();
    } else if (event.key === 'Tab') {
        event.preventDefault();
        const start = codeInput.selectionStart;
        const end = codeInput.selectionEnd;
        codeInput.setRangeText('    ', start, end, 'end');
        updateEditorState();
    } else if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === 'h') {
        // ★回档：Ctrl+Shift+H 快捷打开历史
        event.preventDefault();
        toggleHistory();
    }
});
saveButton.addEventListener('click', saveFile);
deleteButton.addEventListener('click', deleteFile);
document.getElementById('newFileButton').addEventListener('click', createFile);
document.getElementById('refreshButton').addEventListener('click', async () => {
    if (isDirty() && !confirm('刷新会丢弃当前未保存的修改，确定继续吗？')) return;
    if (activePath) await openFile(activePath);
    await loadFiles();
});

loadFiles();
setInterval(async () => { await loadFiles(); await checkExternalChanges(); }, 3000);

/* =========================================================
   ★ 回档模块（独立，不动上面任何逻辑）
   流程：保存时 snapshotVersion() 存一版
         → 🕘 打开侧栏列历史 → 点某版 diff 预览 → 恢复灌入编辑器 → 再 Ctrl+S 生成新版本
   ========================================================= */
let historyPanelOpen = false;
let pendingRestore = null;

const $ = (s) => document.querySelector(s);
function hideHistoryHint() { $('#historyHint').hidden = true; }

// 保存成功后调用：把当前内容作为一版存到服务端
async function snapshotVersion(path, content) {
    try {
        const msg = $('#versionMsg') ? $('#versionMsg').value.trim() : '';
        await api('/api/admin/editor/file/versions', {
            method: 'POST',
            body: JSON.stringify({path, content, message: msg})
        });
        const vmsg = $('#versionMsg');
        if (vmsg) vmsg.value = '';
    } catch (e) {
        // 存版本失败不影响主流程，只悄悄提示
        console.warn('版本快照失败', e.message);
    }
}

function toggleHistory() {
    if (!activePath) { showToast('请先打开一个文件'); return; }
    historyPanelOpen = !historyPanelOpen;
    $('#historyPanel').classList.toggle('hidden', !historyPanelOpen);
    $('#historyPanel').setAttribute('aria-hidden', String(!historyPanelOpen));
    if (historyPanelOpen) loadHistory();
}

$('#historyButton').addEventListener('click', toggleHistory);
$('#historyClose').addEventListener('click', toggleHistory);

// 保存工具栏加一个可选的“版本说明”输入框（没有就忽略）
if (!$('#versionMsg')) {
    const wrap = document.createElement('div');
    wrap.className = 'version-msg-wrap';
    wrap.innerHTML = `<input id="versionMsg" placeholder="本次版本说明（选填，回车保存）" aria-label="版本说明">`;
    $('#saveButton').parentNode.insertBefore(wrap, $('#saveButton'));
    $('#versionMsg').addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); saveButton.click(); }
    });
}

async function loadHistory() {
    const list = $('#historyList');
    list.innerHTML = '<li class="muted">加载中…</li>';
    try {
        const {list: versions} = await api(
            '/api/admin/editor/file/versions?path=' + encodeURIComponent(activePath)
        );
        if (!versions || !versions.length) {
            list.innerHTML = '<li class="muted">暂无历史版本，保存后会自动生成</li>';
            return;
        }
        list.innerHTML = versions.map(v => `
            <li class="history-item" data-id="${v.id}">
                <time>${escapeHtml(v.created_at.replace('T', ' '))}</time>
                <span class="history-msg">${escapeHtml(v.message || '无说明')}</span>
                <div class="history-ops">
                    <button class="link" data-act="diff">对比</button>
                    <button class="link" data-act="restore">恢复</button>
                </div>
            </li>`).join('');
        list.querySelectorAll('.history-item').forEach(li => {
            const id = li.dataset.id;
            li.querySelector('[data-act="diff"]').onclick = () => showDiff(id);
            li.querySelector('[data-act="restore"]').onclick = () => doRestore(id);
        });
    } catch (e) {
        list.innerHTML = `<li class="err">${escapeHtml(e.message)}</li>`;
    }
}

// 拉某版全文，和当前内容做 diff
async function showDiff(vid) {
    const box = $('#diffBox');
    box.innerHTML = '<span class="muted">加载中…</span>';
    $('#diffTitle').textContent = '版本对比';
    pendingRestore = null;
    try {
        const {content} = await api(
            '/api/admin/editor/file/versions/' + encodeURIComponent(vid) + '?path=' + encodeURIComponent(activePath)
        );
        pendingRestore = content;
        box.innerHTML = diffAsHtml(codeInput.value, content);
        $('#diffModal').showModal();
    } catch (e) {
        box.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
    }
}

async function doRestore(vid) {
    const {content} = await api(
        '/api/admin/editor/file/versions/' + encodeURIComponent(vid) + '?path=' + encodeURIComponent(activePath)
    );
    applyRestore(content);
}

function applyRestore(content) {
    // 恢复前若当前有改动，先提醒一次
    if (isDirty() && !confirm('当前未保存的修改会被回档版本替换，确定吗？')) return;
    codeInput.value = content;
    updateEditorState();          // 重新算行号 + 亮“未保存”标记
    hideHistoryHint();
    $('#historyHint').hidden = false;
    $('#diffModal').close();
    codeInput.focus();
    showToast('已回档，按 Ctrl+S 保存即生成新版本');
}

$('#diffRestore').addEventListener('click', () => {
    if (pendingRestore == null) return;
    applyRestore(pendingRestore);
});
$('#diffClose').addEventListener('click', () => $('#diffModal').close());
$('#historyPanel').addEventListener('click', e => {
    // 点侧栏空白不关，点遮罩层才关
    if (e.target === $('#historyPanel')) toggleHistory();
});

// diff 生成：当前内容 = 基准；历史版本 = 红（被删）/ 绿（该版独有）
function diffAsHtml(a, b) {
    const dmp = new diff_match_patch();
    const diffs = dmp.diff_main(a, b);
    dmp.diff_cleanupSemantic(diffs);
    return diffs.map(([op, txt]) => {
        const cls = op === 1 ? 'd-add' : op === -1 ? 'd-del' : '';
        const tag = op === 1 ? 'ins' : op === -1 ? 'del' : 'span';
        return `<${tag} class="${cls}">${escapeHtml(txt)}</${tag}>`;
    }).join('') || '<span class="muted">与当前内容完全一致</span>';
}