function getCourseId() {
    const match = document.body.className.match(/course-(\d+)/);
    if (match) return match[1];
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('id') || "unknown";
}

function injectChatUI() {
    // 1. Создаем кнопку
    const btn = document.createElement('div');
    btn.id = 'moodle-bot-btn';
    btn.innerHTML = '🤖';
    document.body.appendChild(btn);

    const chatWindow = document.createElement('div');
    chatWindow.id = 'moodle-bot-chat';
    chatWindow.innerHTML = `
        <div id="moodle-bot-chat-header">Moodle Assistant</div>
        <div id="moodle-bot-chat-messages">
            <div class="bot-msg">Привет! Я проанализировал оглавление этого курса. Что ищем?</div>
        </div>
        <div id="moodle-bot-chat-input-area">
            <input type="text" id="moodle-bot-chat-input" placeholder="Например: где лаба 3?">
            <button id="moodle-bot-chat-send">▶</button>
        </div>
    `;
    document.body.appendChild(chatWindow);

    const messagesArea = document.getElementById('moodle-bot-chat-messages');
    const sendBtn = document.getElementById('moodle-bot-chat-send');
    const inputField = document.getElementById('moodle-bot-chat-input');

    const isTeacher = document.querySelector('form[action*="editmode.php"]') !== null;
    const isMainPage = window.location.pathname.includes('/course/view.php');

    if (isTeacher && isMainPage) {
        messagesArea.innerHTML += `
            <div class="bot-msg" style="border: 2px solid #ffc107; background: #fffdf5;">
                <b>Режим преподавателя 🎓</b><br>
                Курс обновился? Давайте соберем тексты всех лекций в базу бота!<br>
                <button id="moodle-bot-spider-btn" class="btn btn-sm btn-warning mt-2" style="cursor:pointer; width:100%;">🕷️ Синхронизировать контент</button>
            </div>
        `;

        setTimeout(() => {
            const spiderBtn = document.getElementById('moodle-bot-spider-btn');
            if(spiderBtn) spiderBtn.addEventListener('click', () => runSpider(spiderBtn, messagesArea));
        }, 500);
    }

    btn.addEventListener('click', () => {
        chatWindow.style.display = chatWindow.style.display === 'flex' ? 'none' : 'flex';
        messagesArea.scrollTop = messagesArea.scrollHeight;
    });

    let isSending = false;

    const sendMessage = async () => {
        if (isSending) return;

        const text = inputField.value.trim();
        if (!text) return;

        // Блокируем интерфейс
        isSending = true;
        sendBtn.style.opacity = "0.5";
        inputField.disabled = true;

        messagesArea.innerHTML += `<div class="user-msg">${text}</div>`;
        inputField.value = '';
        messagesArea.scrollTop = messagesArea.scrollHeight;

        const courseId = getCourseId();

        try {
            const response = await fetch(`http://127.0.0.1:8000/api/chat?course_id=${courseId}&message=${encodeURIComponent(text)}`, {
                method: "POST"
            });
            const data = await response.json();

            messagesArea.innerHTML += `<div class="bot-msg">${data.reply}</div>`;
            messagesArea.scrollTop = messagesArea.scrollHeight;

            if (data.target_id) highlightElement(data.target_id);

        } catch (error) {
            messagesArea.innerHTML += `<div class="bot-msg" style="color:red;">Ошибка соединения с сервером! Проверьте бэкенд.</div>`;
            messagesArea.scrollTop = messagesArea.scrollHeight;
        }

        setTimeout(() => {
            isSending = false;
            sendBtn.style.opacity = "1";
            inputField.disabled = false;
            inputField.focus();
        }, 1000);
    };

    sendBtn.addEventListener('click', sendMessage);
    inputField.addEventListener('keypress', (e) => { if (e.key === 'Enter') sendMessage(); });
}

async function runSpider(btnElement, messagesArea) {
    btnElement.disabled = true;
    btnElement.innerText = "⏳ Собираю ссылки...";

    const links = [];
    document.querySelectorAll('li.activity.modtype_page a.aalink, li.activity.modtype_resource a.aalink, li.activity.modtype_assign a.aalink').forEach(a => links.push(a.href));

    const total = links.length;
    let current = 0;
    const courseId = getCourseId();

    messagesArea.innerHTML += `<div class="bot-msg">Найдено ссылок для парсинга: ${total}. Начинаю работу...</div>`;
    messagesArea.scrollTop = messagesArea.scrollHeight;

    for (const link of links) {
        current++;
        btnElement.innerText = `🕷️ ${current} из ${total}`;
        try {
            const response = await fetch(link);
            const htmlText = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(htmlText, "text/html");
            const main = doc.querySelector('[role="main"]');

            if (main) {
                const text = main.innerText.replace(/\s+/g, ' ').trim();
                const urlObj = new URL(link);
                const modId = `module-${urlObj.searchParams.get('id')}`;

                await fetch("http://127.0.0.1:8000/api/module/update", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ course_id: courseId, moodle_id: modId, content_text: text, url: link })
                });
            }
        } catch (err) {
            console.error("Ошибка парсинга:", link);
        }

        await new Promise(r => setTimeout(r, 1000));
    }

    btnElement.innerText = "✅ База обновлена!";
    btnElement.classList.replace('btn-warning', 'btn-success');
    messagesArea.innerHTML += `<div class="bot-msg"><b>Готово!</b> Весь контент сохранен в базу.</div>`;
    messagesArea.scrollTop = messagesArea.scrollHeight;
}

function highlightElement(targetId) {
    const element = document.getElementById(targetId);
    if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        element.classList.add('bot-highlight-animation');
        setTimeout(() => element.classList.remove('bot-highlight-animation'), 4000);
    }
}

async function parseCourseIndex() {
    const courseId = getCourseId();
    if (!courseId || courseId === "unknown") return;

    const courseTitle = document.querySelector('.headermain')?.innerText.trim() || document.title;
    const courseData = { course_id: courseId, title: courseTitle, sections: [] };

    document.querySelectorAll('li.section.main').forEach(sec => {
        if (sec.classList.contains('hidden')) return;
        const sectionData = { moodle_id: sec.id, title: sec.querySelector('.sectionname')?.innerText.trim() || "Без названия", modules: [] };

        sec.querySelectorAll('li.activity').forEach(act => {
            const linkNode = act.querySelector('a.aalink');
            const instanceNameNode = act.querySelector('.instancename');
            let title = "Без названия";
            if (instanceNameNode) {
                const clone = instanceNameNode.cloneNode(true);
                const hiddenSpan = clone.querySelector('.accesshide');
                if (hiddenSpan) hiddenSpan.remove();
                title = clone.innerText.trim();
            }

            let type = "unknown";
            if (act.classList.contains('modtype_assign')) type = "assignment";
            else if (act.classList.contains('modtype_quiz')) type = "quiz";
            else if (act.classList.contains('modtype_resource')) type = "file";
            else if (act.classList.contains('modtype_page')) type = "page";
            else if (act.classList.contains('modtype_forum')) type = "forum";

            sectionData.modules.push({ moodle_id: act.id, type: type, title: title, url: linkNode ? linkNode.href : null });
        });
        if (sectionData.modules.length > 0) courseData.sections.push(sectionData);
    });

    try {
        await fetch("http://127.0.0.1:8000/api/course/sync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(courseData)
        });
    } catch (e) {}
}

setTimeout(() => {
    injectChatUI();

    const path = window.location.pathname;
    if (path.includes('/course/view.php')) {
        parseCourseIndex();
    }
}, 1000);