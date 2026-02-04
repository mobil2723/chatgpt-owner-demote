const form = document.getElementById('loginForm');
const alertBox = document.getElementById('alert');
const submitBtn = document.getElementById('submitBtn');
const togglePassword = document.getElementById('togglePassword');
const passwordInput = document.getElementById('password');

function showAlert(message, type = 'error') {
    alertBox.textContent = message;
    alertBox.style.color = type === 'success' ? '#52e1c6' : '#ff6b6b';
}

togglePassword.addEventListener('click', () => {
    const isPassword = passwordInput.type === 'password';
    passwordInput.type = isPassword ? 'text' : 'password';
    togglePassword.textContent = isPassword ? '隐藏' : '显示';
});

form.addEventListener('submit', async (event) => {
    event.preventDefault();
    alertBox.textContent = '';

    const username = document.getElementById('username').value.trim();
    const password = passwordInput.value;

    if (!username || !password) {
        showAlert('请输入用户名和密码');
        return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = '登录中...';

    try {
        const response = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });

        const data = await response.json();

        if (response.ok && data.success) {
            showAlert('登录成功，正在进入控制台...', 'success');
            setTimeout(() => {
                window.location.href = '/';
            }, 600);
        } else {
            showAlert(data.message || '登录失败');
        }
    } catch (error) {
        showAlert('网络错误，请稍后再试');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = '登录';
    }
});
