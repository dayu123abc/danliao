function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2500);
}

document.getElementById('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const phone = document.getElementById('phone').value.trim();
    const password = document.getElementById('password').value;

    try {
        const res = await fetch('/api/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({phone, password})
        });
        const data = await res.json();
        if (data.ok) {
            showToast('登录成功！');
            setTimeout(() => window.location.href = '/', 800);
        } else {
            showToast(data.error || '登录失败');
        }
    } catch (err) {
        showToast('网络错误，请重试');
    }
});
