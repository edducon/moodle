function injectChatUI() {
    const btn = document.createElement('div');
    btn.id = 'moodle-bot-btn';
    btn.innerHTML = '🤖';
    document.body.appendChild(btn);

    const chatWindow = document.createElement('div');
    chatWindow.id = 'moodle-bot-chat';
    chatWindow.innerHTML = `
        <div id="moodle-bot-chat-header">Moodle Assistant</div>
        <div id="moodle-bot-chat-messages">
            <div class="bot-msg">Привет! Я проанализировал этот курс. Что тебе помочь найти?</div>
        </div>
        <div id="moodle-bot-chat-input-area">
            <input type="text" id="moodle-bot-chat-input" placeholder="Например: где лаба 3?">
            <button id="moodle-bot-chat-send">▶</button>
        </div>
    `;
    document.body.appendChild(chatWindow);

    btn.addEventListener('click', () => {
        chatWindow.style.display = chatWindow.style.display === 'flex' ? 'none' : 'flex';
    });

    const sendBtn = document.getElementById('moodle-bot-chat-send');
    const inputField = document.getElementById('moodle-bot-chat-input');
    const messagesArea = document.getElementById('moodle-bot-chat-messages');

    const sendMessage = async () => {
        const text = inputField.value.trim();
        if (!text) return;

        messagesArea.innerHTML += `<div class="user-msg">${text}</div>`;
        inputField.value = '';
        messagesArea.scrollTop = messagesArea.scrollHeight;

        const urlParams = new URLSearchParams(window.location.search);
        const courseId = urlParams.get('id') || "unknown";

        try {
            const response = await fetch(`http://127.0.0.1:8000/api/chat?course_id=${courseId}&message=${encodeURIComponent(text)}`, {
                method: "POST"
            });
            const data = await response.json();

            messagesArea.innerHTML += `<div class="bot-msg">${data.reply}</div>`;
            messagesArea.scrollTop = messagesArea.scrollHeight;

            if (data.target_id) {
                highlightElement(data.target_id);
            }

        } catch (error) {
            messagesArea.innerHTML += `<div class="bot-msg" style="color:red;">Ошибка соединения с сервером!</div>`;
        }
    };

    sendBtn.addEventListener('click', sendMessage);
    inputField.addEventListener('keypress', (e) => { if (e.key === 'Enter') sendMessage(); });
}

function highlightElement(targetId) {
    const element = document.getElementById(targetId);
    if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        element.classList.add('bot-highlight-animation');
        setTimeout(() => {
            element.classList.remove('bot-highlight-animation');
        }, 4000);
    }
}

async function parseAndSyncCourse() {
    const urlParams = new URLSearchParams(window.location.search);
    const courseId = urlParams.get('id');
    if (!courseId) return;

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
            else if (act.classList.contains('modtype_book')) type = "book";
            else if (act.classList.contains('modtype_scorm')) type = "scorm";
            else if (act.classList.contains('modtype_url')) type = "url";

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
        console.log("Курс синхронизирован с бэкендом в фоне.");
    } catch (e) {}
}

setTimeout(() => {
    injectChatUI();
    parseAndSyncCourse();
}, 1500);