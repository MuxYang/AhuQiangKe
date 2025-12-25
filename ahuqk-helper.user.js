// ==UserScript==
// @name         AHU QiangKe Helper
// @namespace    https://github.com/
// @version      1.0.0
// @description  Export cs-course-select-student-token and student id from jw.ahu.edu.cn after login.
// @author       MuxYang
// @match        https://jw.ahu.edu.cn/*
// @run-at       document-end
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    const FLAG_KEY = 'ahuqk_collect_after_reload';
    const FILE_NAME = 'credentials.json';

    function $(selector) {
        return document.querySelector(selector);
    }

    function createButton() {
        if ($('#ahuqk-helper-btn')) {
            return;
        }
        const btn = document.createElement('button');
        btn.id = 'ahuqk-helper-btn';
        btn.textContent = '导出选课凭据';
        btn.style.position = 'fixed';
        btn.style.right = '24px';
        btn.style.bottom = '24px';
        btn.style.zIndex = '99999';
        btn.style.padding = '10px 14px';
        btn.style.background = '#0f6fff';
        btn.style.color = '#fff';
        btn.style.border = 'none';
        btn.style.borderRadius = '10px';
        btn.style.boxShadow = '0 6px 24px rgba(0,0,0,0.15)';
        btn.style.cursor = 'pointer';
        btn.style.fontSize = '14px';
        btn.style.fontWeight = '600';
        btn.style.opacity = '0.9';
        btn.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
        btn.addEventListener('mouseenter', () => {
            btn.style.opacity = '1';
            btn.style.transform = 'translateY(-2px)';
        });
        btn.addEventListener('mouseleave', () => {
            btn.style.opacity = '0.9';
            btn.style.transform = 'translateY(0)';
        });
        btn.addEventListener('click', () => onButtonClick(btn));
        document.body.appendChild(btn);
    }

    function onButtonClick(btn) {
        if (!isLoggedIn()) {
            alert('请先登录教务系统后再导出。');
            return;
        }
        btn.textContent = '刷新中...';
        btn.disabled = true;
        sessionStorage.setItem(FLAG_KEY, '1');
        location.reload();
    }

    function isLoggedIn() {
        return Boolean(getCookie('cs-course-select-student-token')) || document.cookie.length > 0;
    }

    function getCookie(name) {
        const pattern = new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()\[\]\\\/\+^])/g, '\\$1') + '=([^;]*)');
        const match = document.cookie.match(pattern);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function collectAfterReloadIfNeeded() {
        if (sessionStorage.getItem(FLAG_KEY) === '1') {
            sessionStorage.removeItem(FLAG_KEY);
            setTimeout(collectAndDownload, 800);
        }
    }

    function collectAndDownload() {
        const token = getCookie('cs-course-select-student-token');
        const studentId = findStudentId();

        if (!token) {
            alert('未在 cookie 中找到 cs-course-select-student-token，请确认已登录。');
            return;
        }
        if (!studentId) {
            alert('未匹配到学生 ID，等待选课开放后再试或手动填写。');
        }

        const payload = {
            token: token,
            student_id: studentId || ''
        };

        triggerDownload(payload);
    }

    function findStudentId() {
        const sources = [];
        try {
            sources.push(document.body ? document.body.innerText : '');
        } catch (e) {
            console.warn('读取 body 文本失败', e);
        }
        try {
            sources.push(JSON.stringify(localStorage));
        } catch (e) {
            console.warn('读取 localStorage 失败', e);
        }
        try {
            sources.push(JSON.stringify(sessionStorage));
        } catch (e) {
            console.warn('读取 sessionStorage 失败', e);
        }
        sources.push(document.cookie || '');

        const keywordPattern = /(学号|学生号|studentid|student_id|userId|userid|uid)[^\d]{0,6}(\d{6})/i;
        for (const src of sources) {
            const hit = keywordPattern.exec(src);
            if (hit && hit[2]) {
                return hit[2];
            }
        }

        const loosePattern = /\b\d{6}\b/;
        for (const src of sources) {
            const hit = loosePattern.exec(src);
            if (hit && hit[0]) {
                return hit[0];
            }
        }
        return '';
    }

    function triggerDownload(payload) {
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = FILE_NAME;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);

        showOverlay();
    }

    function showOverlay() {
        if (document.getElementById('ahuqk-overlay')) {
            return;
        }

        const overlay = document.createElement('div');
        overlay.id = 'ahuqk-overlay';
        overlay.style.position = 'fixed';
        overlay.style.inset = '0';
        overlay.style.background = 'rgba(0,0,0,0.65)';
        overlay.style.zIndex = '100000';
        overlay.style.display = 'flex';
        overlay.style.alignItems = 'center';
        overlay.style.justifyContent = 'center';

        const card = document.createElement('div');
        card.style.background = '#ffffff';
        card.style.borderRadius = '12px';
        card.style.padding = '24px 28px';
        card.style.maxWidth = '420px';
        card.style.width = '90%';
        card.style.boxShadow = '0 16px 48px rgba(0,0,0,0.2)';
        card.style.textAlign = 'center';
        card.style.fontFamily = '"Segoe UI", system-ui, sans-serif';

        const title = document.createElement('div');
        title.textContent = '已下载 credentials.json';
        title.style.fontSize = '20px';
        title.style.fontWeight = '700';
        title.style.marginBottom = '12px';

        const desc = document.createElement('div');
        desc.textContent = '文件包含 token 与学生 ID，请检查后放入 AhuQiangKe 根目录。';
        desc.style.fontSize = '14px';
        desc.style.color = '#444';
        desc.style.lineHeight = '1.6';
        desc.style.marginBottom = '18px';

        const btn = document.createElement('button');
        btn.textContent = '好的';
        btn.style.padding = '10px 18px';
        btn.style.background = '#0f6fff';
        btn.style.color = '#fff';
        btn.style.border = 'none';
        btn.style.borderRadius = '10px';
        btn.style.cursor = 'pointer';
        btn.style.fontSize = '14px';
        btn.style.fontWeight = '600';
        btn.style.boxShadow = '0 6px 20px rgba(15,111,255,0.35)';
        btn.addEventListener('click', removeOverlay);

        const closeHint = document.createElement('div');
        closeHint.textContent = '点击空白处也可关闭';
        closeHint.style.fontSize = '12px';
        closeHint.style.color = '#777';
        closeHint.style.marginTop = '10px';

        card.appendChild(title);
        card.appendChild(desc);
        card.appendChild(btn);
        card.appendChild(closeHint);

        overlay.appendChild(card);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                removeOverlay();
            }
        });

        document.body.appendChild(overlay);

        setTimeout(removeOverlay, 8000);
    }

    function removeOverlay() {
        const overlay = document.getElementById('ahuqk-overlay');
        if (overlay) {
            overlay.remove();
        }
    }

    createButton();
    collectAfterReloadIfNeeded();
})();
