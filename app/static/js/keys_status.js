function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
    } else {
        return new Promise((resolve, reject) => {
            const textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.position = "fixed";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {
                const successful = document.execCommand('copy');
                document.body.removeChild(textArea);
                if (successful) {
                    resolve();
                } else {
                    reject(new Error('复制失败'));
                }
            } catch (err) {
                document.body.removeChild(textArea);
                reject(err);
            }
        });
    }
}

function copyKeys(type) {
    const keys = Array.from(document.querySelectorAll(`#${type}Keys .key-text`)).map(span => span.textContent.trim());
    const jsonKeys = JSON.stringify(keys);
    
    copyToClipboard(jsonKeys)
        .then(() => {
            showCopyStatus(`已成功复制${type === 'valid' ? '有效' : '无效'}密钥到剪贴板`);
        })
        .catch((err) => {
            console.error('无法复制文本: ', err);
            showCopyStatus('复制失败，请重试');
        });
}

function copyKey(key) {
    copyToClipboard(key)
        .then(() => {
            showCopyStatus(`已成功复制密钥到剪贴板`);
        })
        .catch((err) => {
            console.error('无法复制文本: ', err);
            showCopyStatus('复制失败，请重试');
        });
}

function showCopyStatus(message, type = 'success', duration = 2000) {
    const statusElement = document.getElementById('copyStatus');
    statusElement.textContent = message;
    
    // 重置所有样式
    statusElement.className = '';
    
    // 添加类型样式
    statusElement.classList.add(type);
    
    // 显示消息
    statusElement.style.opacity = 1;
    
    // 设置定时器隐藏消息
    setTimeout(() => {
        statusElement.style.opacity = 0;
        
        // 彻底清除所有样式
        setTimeout(() => {
            statusElement.className = '';
        }, 300);
    }, duration);
}

async function verifyKey(key, button) {
    try {
        // 获取按钮所在的操作容器
        const actionContainer = button.closest('.key-actions');
        const allButtons = actionContainer.querySelectorAll('button');
        
        // 禁用该行的所有按钮
        allButtons.forEach(btn => {
            btn.disabled = true;
        });
        
        // 保存原始HTML并显示加载状态
        const originalHtml = button.innerHTML;
        button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 验证中';

        const response = await fetch(`/gemini/v1beta/verify-key/${key}`, {
            method: 'POST'
        });
        const data = await response.json();

        // 获取当前行
        const keyRow = button.closest('li');
        
        // 确定当前密钥在哪个列表
        const isInInvalidList = keyRow.closest('#invalidKeys') !== null;

        // 根据验证结果更新UI
        if (data.status === 'valid') {
            showCopyStatus('密钥验证成功', 'success');
            button.style.backgroundColor = '#27ae60';
            
            // 如果密钥在无效列表中且验证成功，将其移动到有效列表
            if (isInInvalidList) {
                // 创建一个API请求来修复密钥状态（重置其失败计数）
                try {
                    // 显示正在处理的状态
                    showCopyStatus('密钥已验证有效，正在修复状态...', 'info', 2000);
                    
                    // 发送请求修复密钥状态
                    const resetResponse = await fetch('/api/keys/reset-status', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ keys: [key] })
                    });
                    
                    const resetResult = await resetResponse.json();
                    
                    if (resetResult.status === 'success') {
                        // 克隆当前行
                        const clonedRow = keyRow.cloneNode(true);
                        
                        // 更新行内的状态标签
                        const statusBadge = clonedRow.querySelector('.status-badge');
                        statusBadge.className = 'status-badge status-valid';
                        statusBadge.innerHTML = '<i class="fas fa-check"></i> 有效';
                        
                        // 将行添加到有效密钥列表
                        document.getElementById('validKeys').appendChild(clonedRow);
                        
                        // 给新添加的行添加验证事件处理程序
                        const newVerifyButton = clonedRow.querySelector('.verify-btn');
                        const newCopyButton = clonedRow.querySelector('.copy-btn');
                        const newRemoveButton = clonedRow.querySelector('.remove-btn');
                        
                        newVerifyButton.onclick = () => verifyKey(key, newVerifyButton);
                        newCopyButton.onclick = () => copyKey(key);
                        newRemoveButton.onclick = () => confirmRemoveKey(key);
                        
                        // 移除原行
                        keyRow.remove();
                        
                        // 显示成功消息
                        showCopyStatus('密钥已移动到有效列表', 'success');
                        
                        // 更新总数显示
                        updateKeyCountDisplay();
                        
                        // 结束函数执行，因为原始行已被移除
                        return;
                    } else {
                        // 如果修复失败，显示错误信息
                        showCopyStatus('无法修复密钥状态：' + (resetResult.message || '未知错误'), 'error', 3000);
                    }
                } catch (resetError) {
                    console.error('修复密钥状态失败:', resetError);
                    showCopyStatus('修复密钥状态失败', 'error');
                }
            }
        } else {
            showCopyStatus('密钥验证失败', 'error');
            button.style.backgroundColor = '#e74c3c';
        }

        // 3秒后恢复按钮原始状态
        setTimeout(() => {
            button.innerHTML = originalHtml;
            
            // 恢复所有按钮
            allButtons.forEach(btn => {
                btn.disabled = false;
            });
            
            // 逐渐恢复背景色
            setTimeout(() => {
                button.style.backgroundColor = '';
            }, 500);
        }, 3000);

    } catch (error) {
        console.error('验证失败:', error);
        showCopyStatus('验证请求失败', 'error');
        
        // 恢复按钮状态
        const actionContainer = button.closest('.key-actions');
        const allButtons = actionContainer.querySelectorAll('button');
        allButtons.forEach(btn => {
            btn.disabled = false;
        });
        
        button.disabled = false;
        button.innerHTML = '<i class="fas fa-check-circle"></i> 验证';
    }
}

// 更新密钥数量显示
function updateKeyCountDisplay() {
    const validCount = document.getElementById('validKeys').children.length;
    const invalidCount = document.getElementById('invalidKeys').children.length;
    const totalCount = validCount + invalidCount;
    
    const totalElement = document.querySelector('.total');
    if (totalElement) {
        totalElement.innerHTML = `<i class="fas fa-key"></i> 总密钥数：${totalCount}`;
    }
}

function scrollToTop() {
    const container = document.querySelector('.container');
    container.scrollTo({
        top: 0,
        behavior: 'smooth'
    });
}

function scrollToBottom() {
    const container = document.querySelector('.container');
    container.scrollTo({
        top: container.scrollHeight,
        behavior: 'smooth'
    });
}

function updateScrollButtons() {
    const container = document.querySelector('.container');
    const scrollButtons = document.querySelector('.scroll-buttons');
    if (container.scrollHeight > container.clientHeight) {
        scrollButtons.style.display = 'flex';
    } else {
        scrollButtons.style.display = 'none';
    }
}

function refreshPage(button) {
    button.classList.add('loading');
    button.disabled = true;
    
    setTimeout(() => {
        window.location.reload();
    }, 300);
}

function toggleSection(header, sectionId) {
    const toggleIcon = header.querySelector('.toggle-icon');
    const content = header.nextElementSibling;
    
    toggleIcon.classList.toggle('collapsed');
    content.classList.toggle('collapsed');
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    // 检查滚动按钮
    updateScrollButtons();

    // 监听展开/折叠事件
    document.querySelectorAll('.key-list h2').forEach(header => {
        header.addEventListener('click', () => {
            setTimeout(updateScrollButtons, 300);
        });
    });

    // 更新版权年份
    const copyrightYear = document.querySelector('.copyright script');
    if (copyrightYear) {
        copyrightYear.textContent = new Date().getFullYear();
    }
});

// Service Worker registration
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/static/service-worker.js')
            .then(registration => {
                console.log('ServiceWorker注册成功:', registration.scope);
            })
            .catch(error => {
                console.log('ServiceWorker注册失败:', error);
            });
    });
}
